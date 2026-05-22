# =============================================================================
# jobs/04_refresh_mp_details.py
# Weekly refresh of MP contact details from the Parliament Members API.
#
# What this script does:
#   1. Fetches all current Commons MPs from the Parliament API in pages of 20.
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
from config import PARLIAMENT_API_BASE, TBL_MP_CONTACTS
from utils.helpers import get_spark, utc_now

from pyspark.sql import Row
from pyspark.sql.types import StructType, StructField, StringType, IntegerType


# =============================================================================
# SETUP
# =============================================================================

spark   = get_spark()
now_iso = utc_now().isoformat()


# =============================================================================
# SCHEMA
# Defined explicitly so Spark does not try to infer types from None values.
# All contact fields are nullable -- not every MP has every detail populated.
# =============================================================================

mp_schema = StructType([
    StructField("member_id",         IntegerType(), True),
    StructField("name",              StringType(),  True),
    StructField("party",             StringType(),  True),
    StructField("constituency_name", StringType(),  True),
    StructField("email",             StringType(),  True),
    StructField("phone",             StringType(),  True),
    StructField("website",           StringType(),  True),
    StructField("twitter_handle",    StringType(),  True),
    StructField("last_refreshed_at", StringType(),  True),
])


# =============================================================================
# FETCH ALL CURRENT MPs
# The Parliament API paginates at 20 per page.
# We page through using skip/take until a partial page signals the end.
# =============================================================================

def fetch_members(base_url: str) -> list:
    """
    Fetch all current Commons MPs from the Parliament Members API.
    Pages through the results 20 at a time.

    Returns:
        List of member dicts from the API response.
    """
    all_members = []
    skip        = 0
    take        = 20

    while True:
        response = requests.get(
            f"{base_url}/Members/Search",
            params={
                "House":           "Commons",
                "IsCurrentMember": "true",
                "take":            take,
                "skip":            skip,
            },
            timeout=30
        )

        if response.status_code != 200:
            raise Exception(
                f"Parliament API failed: {response.status_code} -- {response.text}"
            )

        items = response.json().get("items", [])
        if not items:
            break

        all_members.extend(items)
        print(f"  Fetched {len(all_members)} MPs so far...")

        # A partial page means this is the last one.
        if len(items) < take:
            break

        skip += take

    print(f"Total members fetched: {len(all_members)}")
    return all_members


# =============================================================================
# FETCH CONTACT DETAILS PER MEMBER
# A second API call per member fetches their contact information.
# 647 calls at weekly cadence is acceptable.
# =============================================================================

def fetch_contact(base_url: str, member_id: int) -> dict:
    """
    Fetch contact details for a single MP by their Parliament member ID.

    Returns:
        Dict of contact fields, or empty dict if none found.
    """
    url      = f"{base_url}/Members/{member_id}/Contact"
    response = requests.get(url, timeout=15)

    if response.status_code != 200:
        # Newly elected MPs sometimes have no contact details yet.
        # Return empty dict rather than failing the whole job.
        return {}

    contacts    = response.json().get("value", [])
    contact_map = {}

    for contact in contacts:
        ctype = contact.get("type", "")
        if ctype == "Parliamentary":
            contact_map["email"] = contact.get("email")
            contact_map["phone"] = contact.get("phone")
        if ctype == "Twitter":
            contact_map["twitter"] = contact.get("line1")
        if ctype == "Website":
            contact_map["website"] = contact.get("line1")

    return contact_map


# =============================================================================
# BUILD ROWS
# =============================================================================

members = fetch_members(PARLIAMENT_API_BASE)
mp_rows = []

for i, member in enumerate(members):

    # Log progress every 50 members.
    if i % 50 == 0:
        print(f"  Processing member {i + 1}/{len(members)}...")

    # The member data is nested under a "value" key in the API response.
    # Guard against unexpected response shapes with .get() throughout.
    value     = member.get("value", member)   # Fall back to member itself if no "value" key
    member_id = value.get("id")

    if not member_id:
        print(f"  Warning: skipping member at index {i} -- no id found.")
        continue

    membership        = value.get("latestHouseMembership", {})
    constituency_name = membership.get("membershipFrom")
    contacts          = fetch_contact(PARLIAMENT_API_BASE, member_id)

    mp_rows.append(Row(
        member_id         = int(member_id),
        name              = value.get("nameDisplayAs"),
        party             = value.get("latestParty", {}).get("name"),
        constituency_name = constituency_name,
        email             = contacts.get("email"),
        phone             = contacts.get("phone"),
        website           = contacts.get("website"),
        twitter_handle    = contacts.get("twitter"),
        last_refreshed_at = now_iso
    ))

print(f"Built {len(mp_rows)} MP rows.")


# =============================================================================
# WRITE TO DELTA
# =============================================================================

if not mp_rows:
    raise Exception("No MP rows built -- check Parliament API response structure above.")

mp_df = spark.createDataFrame(mp_rows, schema=mp_schema)
print(f"DataFrame has {mp_df.count()} rows.")

(
    mp_df.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(TBL_MP_CONTACTS)
)

# Verify the write succeeded.
written = spark.table(TBL_MP_CONTACTS).count()
print(f"Verified: {written} rows written to {TBL_MP_CONTACTS}.")

print("MP contact refresh complete.")
dbutils.notebook.exit("success")

