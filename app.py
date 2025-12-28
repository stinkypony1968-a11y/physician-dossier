# Physician Dossier App - Streamlit
# Standalone physician intelligence lookup tool

import streamlit as st
import httpx
import asyncio
from typing import Dict, Any, List, Tuple
import xml.etree.ElementTree as ET
from datetime import datetime
import pandas as pd

# =============================================================================
# CONFIGURATION
# =============================================================================

st.set_page_config(
    page_title="Physician Dossier Lookup",
    page_icon="🩺",
    layout="wide"
)

# API Endpoints
NPI_REGISTRY_API = "https://npiregistry.cms.hhs.gov/api/"
PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# CMS Open Payments Distribution IDs (Socrata/DKAN)
CMS_DISTRIBUTION_IDS = {
    "2024": "4c41c25d-66b8-5fc4-9d98-8d0050d5b4bb",
    "2023": "74707c0a-5cf5-5b1a-a8b8-53588d660e9a",
    "2022": "a7c409e3-a1f8-57d5-8f04-51c2ffe8b77c",
}

# Competitor mapping for CMS payments
COMPETITOR_MAPPING = {
    "MEDTRONIC": "Medtronic", "COVIDIEN": "Medtronic", "EV3": "Medtronic",
    "STRYKER": "Stryker", "CONCENTRIC": "Stryker",
    "PENUMBRA": "Penumbra",
    "MICROVENTION": "MicroVention/Terumo", "TERUMO": "MicroVention/Terumo",
    "CERENOVUS": "J&J/Cerenovus", "JOHNSON & JOHNSON": "J&J/Cerenovus",
    "CODMAN": "J&J/Cerenovus", "DEPUY": "J&J/Cerenovus",
    "RAPID MEDICAL": "Rapid Medical", "PHENOX": "Phenox", "BALT": "Balt",
    "BOSTON SCIENTIFIC": "Boston Scientific", "ABBOTT": "Abbott",
}

TITLE_PREFIXES = {"DR", "DR.", "DOCTOR"}
TITLE_SUFFIXES = {"MD", "M.D.", "DO", "D.O.", "PHD", "PH.D.", "MBA", "MS", "FAANS", "FAHA", "FACS", "JR", "JR.", "SR", "SR.", "II", "III", "IV"}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def parse_physician_name(full_name: str) -> Tuple[str, str, str]:
    """Parse physician name, stripping titles and credentials."""
    if not full_name:
        return "", "", ""

    parts = full_name.strip().split()
    while parts and parts[0].upper().rstrip(",") in TITLE_PREFIXES:
        parts.pop(0)
    while parts and parts[-1].upper().rstrip(",") in TITLE_SUFFIXES:
        parts.pop()
    if parts and "," in parts[-1]:
        parts[-1] = parts[-1].split(",")[0]

    if len(parts) < 2:
        return parts[0] if parts else "", "", " ".join(parts)

    return parts[0], parts[-1], " ".join(parts)


# =============================================================================
# NPI LOOKUP
# =============================================================================

async def lookup_npi(first_name: str, last_name: str, state: str = None, city: str = None) -> Dict[str, Any]:
    """Search NPI Registry."""
    result = {
        "found": False, "npi": None, "verified_name": None,
        "specialty": None, "address": None, "matches": [], "source": "NPI Registry"
    }

    params = {
        "version": "2.1",
        "first_name": first_name,
        "last_name": last_name,
        "limit": 50,
        "enumeration_type": "NPI-1"  # Individual providers only
    }
    if state:
        params["state"] = state
    if city:
        params["city"] = city

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(NPI_REGISTRY_API, params=params)

            if response.status_code != 200:
                return result

            data = response.json()
            results = data.get("results", [])

            if not results:
                return result

            scored_matches = []
            for entry in results:
                basic = entry.get("basic", {})
                addresses = entry.get("addresses", [])
                taxonomies = entry.get("taxonomies", [])

                npi = entry.get("number")
                name = f"{basic.get('first_name', '')} {basic.get('last_name', '')}"

                practice_addr = next((a for a in addresses if a.get("address_purpose") == "LOCATION"), addresses[0] if addresses else {})
                specialty = next((t.get("desc") for t in taxonomies if t.get("primary")), None)

                score = 100
                if state and practice_addr.get("state", "").upper() == state.upper():
                    score += 50
                if city and city.lower() in practice_addr.get("city", "").lower():
                    score += 30

                scored_matches.append({
                    "npi": npi,
                    "name": name,
                    "specialty": specialty,
                    "state": practice_addr.get("state"),
                    "city": practice_addr.get("city"),
                    "organization": practice_addr.get("organization_name"),
                    "score": score
                })

            scored_matches.sort(key=lambda x: x["score"], reverse=True)

            if scored_matches:
                best = scored_matches[0]
                result["found"] = True
                result["npi"] = best["npi"]
                result["verified_name"] = best["name"]
                result["specialty"] = best["specialty"]
                result["address"] = {"state": best["state"], "city": best["city"], "organization": best["organization"]}
                result["matches"] = scored_matches[:5]

            return result

    except Exception as e:
        result["error"] = str(e)
        return result


# =============================================================================
# CMS OPEN PAYMENTS
# =============================================================================

async def fetch_cms_payments(npi: str = None, first_name: str = None, last_name: str = None, years: List[str] = None) -> Dict[str, Any]:
    """Fetch CMS Open Payments data."""
    if years is None:
        years = ["2024", "2023"]

    result = {
        "payments_found": False, "total_amount": 0.0, "relationships": [],
        "by_company": {}, "years_searched": years, "source": "CMS Open Payments"
    }

    all_payments = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        for year in years:
            distribution_id = CMS_DISTRIBUTION_IDS.get(year)
            if not distribution_id:
                continue

            base_url = f"https://openpaymentsdata.cms.gov/api/1/datastore/query/{distribution_id}"

            if npi:
                query_body = {"conditions": [{"property": "covered_recipient_npi", "value": npi, "operator": "="}], "limit": 500}
            elif last_name:
                conditions = [{"property": "covered_recipient_last_name", "value": last_name.upper(), "operator": "="}]
                if first_name:
                    conditions.append({"property": "covered_recipient_first_name", "value": first_name.upper(), "operator": "LIKE"})
                query_body = {"conditions": conditions, "limit": 500}
            else:
                continue

            try:
                response = await client.post(base_url, json=query_body, headers={"Content-Type": "application/json"})

                if response.status_code == 200:
                    data = response.json()
                    payments = data.get("results", [])
                    if payments:
                        all_payments.extend(payments)

                await asyncio.sleep(0.3)

            except Exception as e:
                st.warning(f"CMS {year} error: {e}")

    if all_payments:
        result["payments_found"] = True
        competitor_totals = {}

        for payment in all_payments:
            company = (payment.get("applicable_manufacturer_or_applicable_gpo_making_payment_name") or "").upper()
            try:
                amount = float(payment.get("total_amount_of_payment_usdollars") or 0)
            except:
                amount = 0.0

            competitor = None
            for key, value in COMPETITOR_MAPPING.items():
                if key in company:
                    competitor = value
                    break

            if competitor:
                if competitor not in competitor_totals:
                    competitor_totals[competitor] = {
                        "competitor": competitor,
                        "total_amount": 0.0,
                        "payment_count": 0,
                        "is_jnj": competitor == "J&J/Cerenovus"
                    }
                competitor_totals[competitor]["total_amount"] += amount
                competitor_totals[competitor]["payment_count"] += 1
                if competitor != "J&J/Cerenovus":
                    result["total_amount"] += amount

        result["relationships"] = list(competitor_totals.values())
        result["relationships"].sort(key=lambda x: (x.get("is_jnj", False), -x["total_amount"]))
        result["by_company"] = {r["competitor"]: r["total_amount"] for r in result["relationships"]}

    return result


# =============================================================================
# PUBMED PUBLICATIONS
# =============================================================================

async def fetch_pubmed_publications(first_name: str, last_name: str, max_results: int = 20) -> Dict[str, Any]:
    """Search PubMed for publications."""
    result = {"publications_found": False, "total_count": 0, "publications": [], "source": "PubMed"}

    if not last_name or not first_name:
        return result

    first_initial = first_name[0].upper()
    query = f'{last_name} {first_initial}[Author]'

    pmids = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Search
            search_url = f"{PUBMED_BASE_URL}/esearch.fcgi"
            search_params = {
                "db": "pubmed",
                "term": query,
                "retmax": max_results,
                "retmode": "json",
                "sort": "date"
            }

            response = await client.get(search_url, params=search_params)
            if response.status_code == 200:
                data = response.json()
                pmids = data.get("esearchresult", {}).get("idlist", [])
                result["total_count"] = int(data.get("esearchresult", {}).get("count", 0))

            if not pmids:
                return result

            # Fetch details
            fetch_url = f"{PUBMED_BASE_URL}/efetch.fcgi"
            fetch_params = {
                "db": "pubmed",
                "id": ",".join(pmids[:max_results]),
                "retmode": "xml"
            }

            response = await client.get(fetch_url, params=fetch_params)
            if response.status_code == 200:
                root = ET.fromstring(response.text)

                for article in root.findall(".//PubmedArticle"):
                    try:
                        medline = article.find(".//MedlineCitation")
                        pmid = medline.find(".//PMID").text if medline.find(".//PMID") is not None else None

                        article_elem = medline.find(".//Article")
                        title = article_elem.find(".//ArticleTitle").text if article_elem.find(".//ArticleTitle") is not None else "Untitled"

                        journal = ""
                        journal_elem = article_elem.find(".//Journal/Title")
                        if journal_elem is not None:
                            journal = journal_elem.text

                        year = None
                        pub_date = article_elem.find(".//Journal/JournalIssue/PubDate")
                        if pub_date is not None:
                            year_elem = pub_date.find("Year")
                            if year_elem is not None:
                                year = int(year_elem.text)

                        result["publications"].append({
                            "pmid": pmid,
                            "title": title,
                            "journal": journal,
                            "year": year,
                            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None
                        })
                    except:
                        continue

                result["publications_found"] = len(result["publications"]) > 0

        except Exception as e:
            st.warning(f"PubMed error: {e}")

    return result


# =============================================================================
# MAIN PIPELINE
# =============================================================================

async def run_dossier_pipeline(physician_name: str, state: str = None, city: str = None) -> Dict[str, Any]:
    """Run the full dossier pipeline."""

    # Parse name
    first_name, last_name, full_name = parse_physician_name(physician_name)

    if not last_name:
        return {"error": "Could not parse physician name"}

    result = {
        "physician_name": physician_name,
        "parsed_name": {"first": first_name, "last": last_name, "full": full_name},
        "npi_data": None,
        "cms_data": None,
        "publications": None,
        "timestamp": datetime.now().isoformat()
    }

    # Step 1: NPI Lookup
    npi_result = await lookup_npi(first_name, last_name, state, city)
    result["npi_data"] = npi_result

    npi = npi_result.get("npi") if npi_result.get("found") else None

    # Step 2: CMS Payments
    cms_result = await fetch_cms_payments(
        npi=npi,
        first_name=first_name,
        last_name=last_name
    )
    result["cms_data"] = cms_result

    # Step 3: PubMed
    pubmed_result = await fetch_pubmed_publications(first_name, last_name)
    result["publications"] = pubmed_result

    return result


# =============================================================================
# STREAMLIT UI
# =============================================================================

def main():
    st.title("🩺 Physician Dossier Lookup")
    st.markdown("*Generate comprehensive physician intelligence from public data sources*")

    # Sidebar
    with st.sidebar:
        st.header("About")
        st.markdown("""
        This tool aggregates physician information from:
        - **NPI Registry** - National Provider Identifier
        - **CMS Open Payments** - Industry payment data
        - **PubMed** - Research publications
        """)

        st.divider()
        st.markdown("**Data Sources:**")
        st.markdown("- NPI: npiregistry.cms.hhs.gov")
        st.markdown("- CMS: openpaymentsdata.cms.gov")
        st.markdown("- PubMed: pubmed.ncbi.nlm.nih.gov")

    # Input form
    col1, col2, col3 = st.columns([3, 1, 1])

    with col1:
        physician_name = st.text_input(
            "Physician Name",
            placeholder="Dr. John Smith, MD",
            help="Enter the physician's name (titles and credentials will be stripped automatically)"
        )

    with col2:
        state = st.selectbox(
            "State (optional)",
            options=[""] + list(US_STATES.keys()),
            format_func=lambda x: f"{x} - {US_STATES[x]}" if x else "Any State"
        )

    with col3:
        city = st.text_input("City (optional)", placeholder="Seattle")

    # Search button
    if st.button("🔍 Generate Dossier", type="primary", use_container_width=True):
        if not physician_name:
            st.error("Please enter a physician name")
            return

        with st.spinner("Gathering intelligence..."):
            result = asyncio.run(run_dossier_pipeline(
                physician_name,
                state=state if state else None,
                city=city if city else None
            ))

        if "error" in result:
            st.error(result["error"])
            return

        # Display results
        st.divider()

        # NPI Section
        st.subheader("👤 Provider Information")
        npi_data = result.get("npi_data", {})

        if npi_data.get("found"):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("NPI Number", npi_data.get("npi", "N/A"))
            with col2:
                st.metric("Verified Name", npi_data.get("verified_name", "N/A"))
            with col3:
                addr = npi_data.get("address", {})
                st.metric("Location", f"{addr.get('city', 'N/A')}, {addr.get('state', 'N/A')}")

            if npi_data.get("specialty"):
                st.info(f"**Specialty:** {npi_data['specialty']}")

            # Show other matches
            if len(npi_data.get("matches", [])) > 1:
                with st.expander("Other potential matches"):
                    for match in npi_data["matches"][1:5]:
                        st.markdown(f"- **{match['name']}** (NPI: {match['npi']}) - {match.get('city', 'N/A')}, {match.get('state', 'N/A')}")
        else:
            st.warning("No NPI match found. CMS data will be searched by name.")

        st.divider()

        # CMS Payments Section
        st.subheader("💰 Industry Payments (CMS Open Payments)")
        cms_data = result.get("cms_data", {})

        if cms_data.get("payments_found"):
            total = cms_data.get("total_amount", 0)
            relationships = cms_data.get("relationships", [])

            st.metric("Total Competitor Payments", f"${total:,.2f}", help="Excludes J&J payments")

            if relationships:
                # Create dataframe for chart
                df = pd.DataFrame(relationships)
                df = df[df["total_amount"] > 0].sort_values("total_amount", ascending=True)

                if not df.empty:
                    st.bar_chart(df.set_index("competitor")["total_amount"])

                # Details table
                st.markdown("**Payment Details:**")
                for rel in relationships:
                    jnj_badge = " 🔵" if rel.get("is_jnj") else ""
                    st.markdown(f"- **{rel['competitor']}**{jnj_badge}: ${rel['total_amount']:,.2f} ({rel['payment_count']} payments)")
        else:
            st.info("No CMS payment records found for this physician.")

        st.divider()

        # Publications Section
        st.subheader("📚 Research Publications (PubMed)")
        pub_data = result.get("publications", {})

        if pub_data.get("publications_found"):
            st.metric("Publications Found", pub_data.get("total_count", 0))

            publications = pub_data.get("publications", [])
            for pub in publications[:10]:
                with st.container():
                    title = pub.get("title", "Untitled")
                    year = pub.get("year", "N/A")
                    journal = pub.get("journal", "")
                    url = pub.get("url", "")

                    if url:
                        st.markdown(f"**[{title}]({url})** ({year})")
                    else:
                        st.markdown(f"**{title}** ({year})")
                    if journal:
                        st.caption(journal)
        else:
            st.info("No publications found for this physician.")

        # Download JSON
        st.divider()
        st.download_button(
            label="📥 Download Full Dossier (JSON)",
            data=str(result),
            file_name=f"dossier_{physician_name.replace(' ', '_')}.json",
            mime="application/json"
        )


if __name__ == "__main__":
    main()
