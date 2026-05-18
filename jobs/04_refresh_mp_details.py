# =============================================================================
# jobs/04_refresh_mp_details.py
# Weekly refresh of MP contact details from the Parliament Members API.
#
# What this script does:
#   1. Fetches all current Commons MPs from the Parliament API.
#   2. For each MP, retrieves their constituency linkage and contact details.
#   3. Overwrites mp_contact_details in full.
#
# Run schedule: weekly, after 03_refresh_ea_flood_areas completes.
# After a general election, run immediately and also run 05_refresh_boundaries.
#
# The Parliament API is public and requires no authentication key.
# =============================================================================

import sys
import requests

sys.path.insert(0, "/Workspace/Users/jon.payne@environment-agency.gov.uk/FGS_Notebooks/")
from config import PARLIAMENT_API_BASE, PARLIAMENT_MEMBERS_PATH, TBL_MP_CONTACTS
from utils.helpers import get_spark, utc_now

from pyspark.sql import Row


# =============================================================================
# SETUP
# =============================================================================

spark = get_spark()
now   = utc_now()


# =============================================================================
# FETCH ALL CURRENT MPs
# The Parliament API paginates at 650 max -- one page covers the full Commons.
# If more than 650 MPs ever exist, add pagination here.
# =============================================================================

def fetch_members(base_url: str, path: str) -> list:
    """
    Fetch all current Commons MPs from the Parliament Members API.

    Returns:
        List of member dicts from the API response.
    """
    url = f"{base_url}{path}"
    print(f"Fetching MPs from Parliament API: {url}")

    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Parliament API failed: {response.status_code} -- {response.text}")

    data = response.json()
    members = data.get("items", [])
    print(f"  Retrieved {len(members)} members.")
    return members


# =============================================================================
# FETCH CONTACT DETAILS PER MEMBER
# The search endpoint returns basic member info but not contact details.
# A second API call per member fetches their contact information.
# This makes N+1 API calls. With 650 MPs, this is acceptable at weekly cadence.
# =============================================================================

def fetch_contact(base_url: str, member_id: int) -> dict:
    """
    Fetch contact details for a single MP by their Parliament member ID.

    Args:
        base_url:  Parliament API base URL (from config.py).
        member_id: The integer member ID returned by the search endpoint.

    Returns:
        Dict of contact details, or empty dict if none found.
    """
    url = f"{base_url}/Members/{member_id}/Contact"
    response = requests.get(url, timeout=15)

    if response.status_code != 200:
        # Contact details are sometimes unavailable for newly elected MPs.
        # Return an empty dict rather than failing the whole job.
        print(f"  Warning: no contact details for member {member_id} (status {response.status_code})")
        return {}

    contacts = response.json().get("value", [])

    # The API returns a list of contact type objects.
    # We want the Parliamentary contact (not constituency office etc.)
    # Types include "Parliamentary", "Constituency", "Twitter", "Website" etc.
    contact_map = {}
    for contact in contacts:
        ctype = contact.get("type", "")
        if ctype == "Parliamentary":
            contact_map["email"] = contact.get("email")
            contact_map["phone"] = contact.get("phone")
            contact_map["line1"] = contact.get("line1")
        if ctype == "Twitter":
            contact_map["twitter"] = contact.get("line1")
        if ctype == "Website":
            contact_map["website"] = contact.get("line1")

    return contact_map


# =============================================================================
# BUILD ROWS
# =============================================================================

members = fetch_members(PARLIAMENT_API_BASE, PARLIAMENT_MEMBERS_PATH)

mp_rows = []

for i, member in enumerate(members):
    member_id = member["value"]["id"]

    # Log progress every 50 members so you can see it working.
    if i % 50 == 0:
        print(f"  Processing member {i+1}/{len(members)}...")

    # The member's constituency is nested under latestHouseMembership.
    membership = member["value"].get("latestHouseMembership", {})
    constituency_name = membership.get("membershipFrom")

    # The ONS GSS code is not returned directly by the Parliament API.
    # We join to parliamentary_constituencies on constituency name in the
    # lookup table computation (06_compute_lookup.py).
    # Store the name here as the join key.

    contacts = fetch_contact(PARLIAMENT_API_BASE, member_id)

    mp_rows.append(Row(
        member_id          = member_id,
        name               = member["value"].get("nameDisplayAs"),
        party              = member["value"].get("latestParty", {}).get("name"),
        constituency_name  = constituency_name,
        email              = contacts.get("email"),
        phone              = contacts.get("phone"),
        website            = contacts.get("website"),
        twitter_handle     = contacts.get("twitter"),
        last_refreshed_at  = now.isoformat()
    ))

print(f"Parsed {len(mp_rows)} MP rows.")
print(mp_rows)

# =============================================================================
# WRITE TO DELTA
# Full overwrite each week. After a by-election, the old MP row is replaced
# with the new one. There is no value in keeping stale MP records here --
# the FGS intersection tables carry the statement_id which provides temporal
# context if you ever need to trace who held a seat at a given time.
# =============================================================================

mp_df = spark.createDataFrame(mp_rows)
(
    mp_df.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    #.saveAsTable(TBL_MP_CONTACTS)
)
print(f"Written {len(mp_rows)} rows to {TBL_MP_CONTACTS}.")

print("MP contact refresh complete.")
dbutils.notebook.exit("success")
