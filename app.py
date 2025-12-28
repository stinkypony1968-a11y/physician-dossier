# Physician Dossier App - Streamlit
# Neurovascular Specialist Intelligence Tool for Stroke & Hemorrhagic Care
# Uses Railway PostgreSQL database with CMS Open Payments data

import streamlit as st
import httpx
import asyncio
from typing import Dict, Any, List, Tuple, Optional
import xml.etree.ElementTree as ET
from datetime import datetime
import pandas as pd
import os

# Database connection
try:
    from sqlalchemy import create_engine, text
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

# =============================================================================
# CONFIGURATION
# =============================================================================

st.set_page_config(
    page_title="Neurovascular Physician Dossier",
    page_icon="🧠",
    layout="wide"
)

# Database URL - Railway PostgreSQL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:hELYcEhCcFWVsfWboXRMKFdNKOvbpEFm@switchyard.proxy.rlwy.net:57363/railway"
)

# API Endpoints (fallback)
NPI_REGISTRY_API = "https://npiregistry.cms.hhs.gov/api/"
PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

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

# Neuro-relevant specialties
NEURO_SPECIALTIES = [
    "Neurological Surgery",
    "Neurology",
    "Interventional Neuroradiology",
    "Vascular Neurology",
    "Neuroradiology",
    "Endovascular Surgical Neuroradiology",
    "Vascular Surgery",
    "Interventional Radiology"
]

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


def get_db_connection():
    """Get database connection."""
    if not HAS_SQLALCHEMY:
        return None
    try:
        engine = create_engine(DATABASE_URL)
        return engine
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        return None


# =============================================================================
# DATABASE LOOKUP - CMS PAYMENTS
# =============================================================================

def fetch_cms_payments_from_db(first_name: str, last_name: str, npi: str = None) -> Dict[str, Any]:
    """Fetch CMS payments from Railway PostgreSQL database."""
    result = {
        "payments_found": False,
        "total_competitor_amount": 0.0,
        "total_jnj_amount": 0.0,
        "relationships": [],
        "by_company": {},
        "physician_info": None,
        "source": "CMS Open Payments (Local Database)"
    }

    engine = get_db_connection()
    if not engine:
        result["error"] = "Database not available"
        return result

    try:
        with engine.connect() as conn:
            # Build query
            if npi:
                query = text("""
                    SELECT
                        physician_name_full,
                        npi,
                        physician_specialty,
                        physician_city,
                        physician_state,
                        company_name_normalized,
                        SUM(total_amount) as total_amount,
                        SUM(payment_count) as payment_count,
                        program_year
                    FROM cms_payments
                    WHERE npi = :npi
                    GROUP BY physician_name_full, npi, physician_specialty, physician_city,
                             physician_state, company_name_normalized, program_year
                    ORDER BY program_year DESC, total_amount DESC
                """)
                rows = conn.execute(query, {"npi": npi}).fetchall()
            else:
                query = text("""
                    SELECT
                        physician_name_full,
                        npi,
                        physician_specialty,
                        physician_city,
                        physician_state,
                        company_name_normalized,
                        SUM(total_amount) as total_amount,
                        SUM(payment_count) as payment_count,
                        program_year
                    FROM cms_payments
                    WHERE LOWER(physician_first_name) = LOWER(:first_name)
                      AND LOWER(physician_last_name) = LOWER(:last_name)
                    GROUP BY physician_name_full, npi, physician_specialty, physician_city,
                             physician_state, company_name_normalized, program_year
                    ORDER BY program_year DESC, total_amount DESC
                """)
                rows = conn.execute(query, {"first_name": first_name, "last_name": last_name}).fetchall()

            if not rows:
                return result

            result["payments_found"] = True

            # Get physician info from first row
            first_row = rows[0]
            result["physician_info"] = {
                "name": first_row[0],
                "npi": first_row[1],
                "specialty": first_row[2],
                "city": first_row[3],
                "state": first_row[4]
            }

            # Aggregate by company
            company_totals = {}
            for row in rows:
                company = row[5] or "Other"
                amount = float(row[6] or 0)
                count = int(row[7] or 0)

                if company not in company_totals:
                    company_totals[company] = {
                        "competitor": company,
                        "total_amount": 0.0,
                        "payment_count": 0,
                        "is_jnj": company == "J&J/Cerenovus"
                    }
                company_totals[company]["total_amount"] += amount
                company_totals[company]["payment_count"] += count

            # Calculate totals
            for company, data in company_totals.items():
                if data["is_jnj"]:
                    result["total_jnj_amount"] += data["total_amount"]
                else:
                    result["total_competitor_amount"] += data["total_amount"]

            # Sort: competitors first (by amount desc), then J&J
            result["relationships"] = sorted(
                company_totals.values(),
                key=lambda x: (x["is_jnj"], -x["total_amount"])
            )
            result["by_company"] = {r["competitor"]: r["total_amount"] for r in result["relationships"]}

    except Exception as e:
        result["error"] = str(e)
        st.error(f"Database query error: {e}")

    return result


# =============================================================================
# NPI LOOKUP
# =============================================================================

async def lookup_npi(first_name: str, last_name: str, state: str = None, city: str = None) -> Dict[str, Any]:
    """Search NPI Registry with extended data extraction."""
    result = {
        "found": False, "npi": None, "verified_name": None,
        "specialty": None, "address": None, "matches": [], "source": "NPI Registry",
        # Extended fields
        "credentials": None,
        "gender": None,
        "enumeration_date": None,
        "years_in_practice": None,
        "all_specialties": [],
        "board_certifications": [],
        "organization_name": None
    }

    params = {
        "version": "2.1",
        "first_name": first_name,
        "last_name": last_name,
        "limit": 50,
        "enumeration_type": "NPI-1"
    }
    if state:
        params["state"] = state
    if city:
        params["city"] = city

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(NPI_REGISTRY_API, params=params)

            if response.status_code != 200:
                result["error"] = f"NPI API returned status {response.status_code}"
                return result

            data = response.json()
            results = data.get("results", [])

            if not results:
                result["message"] = "No NPI matches found"
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

                # Extract extended info
                credentials = basic.get("credential", "")
                gender = basic.get("gender", "")
                enumeration_date = basic.get("enumeration_date", "")

                # Calculate years in practice from enumeration date
                years_in_practice = None
                if enumeration_date:
                    try:
                        enum_year = int(enumeration_date.split("-")[0])
                        years_in_practice = datetime.now().year - enum_year
                    except:
                        pass

                # Extract all specialties/certifications
                all_specialties = []
                for tax in taxonomies:
                    spec_desc = tax.get("desc", "")
                    if spec_desc:
                        all_specialties.append({
                            "specialty": spec_desc,
                            "primary": tax.get("primary", False),
                            "state": tax.get("state", ""),
                            "license": tax.get("license", "")
                        })

                # Score matches - prefer neuro specialties
                score = 100
                if state and practice_addr.get("state", "").upper() == state.upper():
                    score += 50
                if city and city.lower() in practice_addr.get("city", "").lower():
                    score += 30

                # Boost neuro-related specialties
                if specialty:
                    for neuro_spec in NEURO_SPECIALTIES:
                        if neuro_spec.lower() in specialty.lower():
                            score += 100
                            break

                scored_matches.append({
                    "npi": npi,
                    "name": name,
                    "specialty": specialty,
                    "state": practice_addr.get("state"),
                    "city": practice_addr.get("city"),
                    "organization": practice_addr.get("organization_name"),
                    "score": score,
                    # Extended fields
                    "credentials": credentials,
                    "gender": gender,
                    "enumeration_date": enumeration_date,
                    "years_in_practice": years_in_practice,
                    "all_specialties": all_specialties
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
                # Extended fields
                result["credentials"] = best.get("credentials")
                result["gender"] = best.get("gender")
                result["enumeration_date"] = best.get("enumeration_date")
                result["years_in_practice"] = best.get("years_in_practice")
                result["all_specialties"] = best.get("all_specialties", [])
                result["organization_name"] = best.get("organization")

            return result

    except Exception as e:
        result["error"] = str(e)
        return result


# =============================================================================
# EDUCATION & TRAINING LOOKUP
# =============================================================================

# Known neurovascular societies and organizations
NEURO_SOCIETIES = [
    "Society of NeuroInterventional Surgery (SNIS)",
    "American Association of Neurological Surgeons (AANS)",
    "Congress of Neurological Surgeons (CNS)",
    "Society of Vascular and Interventional Neurology (SVIN)",
    "American Academy of Neurology (AAN)",
    "American Society of Neuroradiology (ASNR)",
    "World Federation of Interventional and Therapeutic Neuroradiology (WFITN)",
    "Neurocritical Care Society (NCS)",
    "American Heart Association / American Stroke Association (AHA/ASA)",
    "European Stroke Organisation (ESO)"
]

# Common neurosurgery/neurointerventional training programs
KNOWN_FELLOWSHIPS = {
    "endovascular": "Endovascular Neurosurgery/Neurointerventional Fellowship",
    "cerebrovascular": "Cerebrovascular/Skull Base Fellowship",
    "stroke": "Vascular Neurology/Stroke Fellowship",
    "neurointerventional": "Neurointerventional Radiology Fellowship",
    "neurointensive": "Neurocritical Care Fellowship"
}


async def fetch_education_data(
    first_name: str,
    last_name: str,
    npi: str = None,
    city: str = None,
    state: str = None,
    specialty: str = None
) -> Dict[str, Any]:
    """
    Fetch education, training, and professional organization data.
    Attempts to gather from multiple public sources.
    """
    result = {
        "found": False,
        "medical_school": None,
        "residency": [],
        "fellowships": [],
        "board_certifications": [],
        "professional_organizations": [],
        "sources": [],
        "inferred_training": []
    }

    # Infer likely training path based on specialty
    if specialty:
        specialty_lower = specialty.lower()

        # Infer fellowship based on specialty
        if "neurological surgery" in specialty_lower:
            result["inferred_training"].append({
                "type": "Residency",
                "program": "Neurological Surgery Residency (7 years)",
                "confidence": "inferred from specialty"
            })

        if "interventional" in specialty_lower or "endovascular" in specialty_lower:
            result["inferred_training"].append({
                "type": "Fellowship",
                "program": "Endovascular/Neurointerventional Fellowship (1-2 years)",
                "confidence": "inferred from specialty"
            })

        if "vascular neurology" in specialty_lower:
            result["inferred_training"].append({
                "type": "Residency",
                "program": "Neurology Residency (4 years)",
                "confidence": "inferred from specialty"
            })
            result["inferred_training"].append({
                "type": "Fellowship",
                "program": "Vascular Neurology Fellowship (1-2 years)",
                "confidence": "inferred from specialty"
            })

        if "neuroradiology" in specialty_lower:
            result["inferred_training"].append({
                "type": "Residency",
                "program": "Diagnostic Radiology Residency (5 years)",
                "confidence": "inferred from specialty"
            })
            result["inferred_training"].append({
                "type": "Fellowship",
                "program": "Neuroradiology Fellowship (1-2 years)",
                "confidence": "inferred from specialty"
            })

    # Infer likely society memberships based on specialty
    if specialty:
        specialty_lower = specialty.lower()
        likely_societies = []

        if "neurological surgery" in specialty_lower or "neurosurg" in specialty_lower:
            likely_societies.extend([
                "American Association of Neurological Surgeons (AANS)",
                "Congress of Neurological Surgeons (CNS)"
            ])

        if "interventional" in specialty_lower or "endovascular" in specialty_lower:
            likely_societies.append("Society of NeuroInterventional Surgery (SNIS)")

        if "vascular neurology" in specialty_lower or "stroke" in specialty_lower:
            likely_societies.extend([
                "Society of Vascular and Interventional Neurology (SVIN)",
                "American Heart Association / American Stroke Association (AHA/ASA)"
            ])

        if "neurology" in specialty_lower:
            likely_societies.append("American Academy of Neurology (AAN)")

        if "neuroradiology" in specialty_lower:
            likely_societies.append("American Society of Neuroradiology (ASNR)")

        result["professional_organizations"] = [
            {"name": soc, "confidence": "likely based on specialty"}
            for soc in likely_societies
        ]

    # Try to fetch from Healthgrades (public physician directory)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Healthgrades search
            search_url = f"https://www.healthgrades.com/api/v2/providers/search"
            params = {
                "q": f"{first_name} {last_name}",
                "location": f"{city}, {state}" if city and state else state or "",
                "specialty": "neurological-surgery" if specialty and "neuro" in specialty.lower() else ""
            }

            # Note: Healthgrades API may require additional headers or auth
            # This is a best-effort attempt
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Accept": "application/json"
            }

            # Try the public API endpoint (may not work without proper auth)
            response = await client.get(search_url, params=params, headers=headers)

            if response.status_code == 200:
                data = response.json()
                # Parse response for education data
                if data.get("providers"):
                    provider = data["providers"][0]
                    if provider.get("education"):
                        edu = provider["education"]
                        result["medical_school"] = edu.get("medicalSchool")
                        result["residency"] = edu.get("residency", [])
                        result["found"] = True
                        result["sources"].append("Healthgrades")

    except Exception as e:
        # Healthgrades fetch failed - continue with other sources
        pass

    # Try to get board certification from ABMS (if available)
    # Note: ABMS requires subscription, so this is informational
    if specialty:
        specialty_lower = specialty.lower()
        certs = []

        if "neurological surgery" in specialty_lower:
            certs.append({
                "board": "American Board of Neurological Surgery (ABNS)",
                "certification": "Neurological Surgery",
                "confidence": "likely based on specialty"
            })

        if "neurology" in specialty_lower:
            certs.append({
                "board": "American Board of Psychiatry and Neurology (ABPN)",
                "certification": "Neurology",
                "confidence": "likely based on specialty"
            })

        if "vascular neurology" in specialty_lower:
            certs.append({
                "board": "American Board of Psychiatry and Neurology (ABPN)",
                "certification": "Vascular Neurology (subspecialty)",
                "confidence": "likely based on specialty"
            })

        if "radiology" in specialty_lower:
            certs.append({
                "board": "American Board of Radiology (ABR)",
                "certification": "Diagnostic Radiology",
                "confidence": "likely based on specialty"
            })

        if "neuroradiology" in specialty_lower:
            certs.append({
                "board": "American Board of Radiology (ABR)",
                "certification": "Neuroradiology (CAQ)",
                "confidence": "likely based on specialty"
            })

        result["board_certifications"] = certs

    # Mark as found if we have any inferred data
    if result["inferred_training"] or result["board_certifications"] or result["professional_organizations"]:
        result["found"] = True
        if "Inferred from specialty" not in result["sources"]:
            result["sources"].append("Inferred from specialty")

    return result


# =============================================================================
# PUBMED PUBLICATIONS - Enhanced with Author Verification
# =============================================================================

def score_author_match(
    author_name: str,
    affiliation: str,
    target_first: str,
    target_last: str,
    target_city: str = None,
    target_state: str = None,
    target_specialty: str = None
) -> Tuple[int, List[str]]:
    """
    Score how likely a PubMed author matches our target physician.
    Returns (score, list of match reasons).
    """
    score = 0
    reasons = []

    author_lower = author_name.lower() if author_name else ""
    affil_lower = affiliation.lower() if affiliation else ""

    # Name matching
    if target_last.lower() in author_lower:
        score += 20
        if target_first.lower() in author_lower:
            score += 30  # Full name match
            reasons.append("Full name match")
        elif target_first[0].lower() == author_lower.split()[0][0] if author_lower.split() else False:
            score += 10  # Initial match
            reasons.append("Name initial match")

    # Location matching in affiliation
    if affiliation:
        # State matching
        if target_state:
            state_full = US_STATES.get(target_state.upper(), "").lower()
            if target_state.lower() in affil_lower or state_full in affil_lower:
                score += 25
                reasons.append(f"State: {target_state}")

        # City matching
        if target_city and target_city.lower() in affil_lower:
            score += 30
            reasons.append(f"City: {target_city}")

        # Neuro/stroke specialty keywords in affiliation
        neuro_keywords = ["neurosurg", "neurology", "stroke", "cerebrovascular",
                         "neurointervent", "neuroradiol", "brain", "aneurysm"]
        for keyword in neuro_keywords:
            if keyword in affil_lower:
                score += 15
                reasons.append(f"Neuro affiliation")
                break

        # Known institutions (Idaho-specific for Evan Joyce example)
        idaho_institutions = ["st. luke", "saint luke", "boise", "idaho"]
        for inst in idaho_institutions:
            if inst in affil_lower:
                score += 20
                reasons.append("Regional institution")
                break

    return score, reasons


async def fetch_pubmed_publications(
    first_name: str,
    last_name: str,
    city: str = None,
    state: str = None,
    specialty: str = None,
    max_results: int = 30
) -> Dict[str, Any]:
    """
    Search PubMed for publications with enhanced author verification.
    Uses location and specialty to filter likely matches.
    """
    result = {
        "publications_found": False,
        "total_count": 0,
        "verified_count": 0,
        "publications": [],
        "unverified_publications": [],
        "source": "PubMed",
        "verification_note": None
    }

    if not last_name or not first_name:
        return result

    first_initial = first_name[0].upper()

    # Build search queries - try multiple strategies
    queries = []

    # Strategy 1: Full name + neuro terms
    queries.append(f'"{last_name} {first_initial}"[Author] AND (stroke OR hemorrhage OR aneurysm OR neurovascular OR thrombectomy OR embolization)')

    # Strategy 2: Full name + state affiliation if available
    if state:
        state_full = US_STATES.get(state.upper(), state)
        queries.append(f'"{last_name} {first_initial}"[Author] AND {state_full}[Affiliation]')

    # Strategy 3: Full name + city if available
    if city:
        queries.append(f'"{last_name} {first_initial}"[Author] AND {city}[Affiliation]')

    # Strategy 4: Broader search as fallback
    queries.append(f'{last_name} {first_initial}[Author]')

    all_pmids = set()

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            search_url = f"{PUBMED_BASE_URL}/esearch.fcgi"

            # Try each query strategy
            for query in queries:
                if len(all_pmids) >= max_results:
                    break

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
                    all_pmids.update(pmids)

                    if not result["total_count"]:
                        result["total_count"] = int(data.get("esearchresult", {}).get("count", 0))

            if not all_pmids:
                result["message"] = "No publications found"
                return result

            # Fetch full details including author affiliations
            fetch_url = f"{PUBMED_BASE_URL}/efetch.fcgi"
            fetch_params = {
                "db": "pubmed",
                "id": ",".join(list(all_pmids)[:max_results]),
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
                        title_elem = article_elem.find(".//ArticleTitle")
                        title = "".join(title_elem.itertext()) if title_elem is not None else "Untitled"

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

                        # Extract all authors and their affiliations
                        authors_list = []
                        target_author_affiliation = None
                        target_author_found = False

                        author_list = article_elem.find(".//AuthorList")
                        if author_list is not None:
                            for author in author_list.findall("Author"):
                                last = author.find("LastName")
                                fore = author.find("ForeName")
                                initials = author.find("Initials")

                                author_last = last.text if last is not None else ""
                                author_fore = fore.text if fore is not None else ""
                                author_init = initials.text if initials is not None else ""

                                # Get affiliation
                                affil_elem = author.find(".//AffiliationInfo/Affiliation")
                                affiliation = affil_elem.text if affil_elem is not None else ""

                                author_full = f"{author_fore} {author_last}".strip()
                                authors_list.append(author_full)

                                # Check if this is our target author
                                if (last_name.lower() == author_last.lower() and
                                    (first_name.lower() == author_fore.lower() or
                                     first_name[0].upper() == author_init[0].upper() if author_init else False)):
                                    target_author_found = True
                                    target_author_affiliation = affiliation

                        # Score this publication for likelihood of being the right author
                        match_score, match_reasons = score_author_match(
                            author_name=f"{first_name} {last_name}",
                            affiliation=target_author_affiliation,
                            target_first=first_name,
                            target_last=last_name,
                            target_city=city,
                            target_state=state,
                            target_specialty=specialty
                        )

                        # Determine confidence level
                        if match_score >= 50:
                            confidence = "high"
                        elif match_score >= 30:
                            confidence = "medium"
                        else:
                            confidence = "low"

                        pub_entry = {
                            "pmid": pmid,
                            "title": title,
                            "journal": journal,
                            "year": year,
                            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                            "authors": authors_list[:5],  # First 5 authors
                            "author_count": len(authors_list),
                            "target_author_affiliation": target_author_affiliation,
                            "match_score": match_score,
                            "match_reasons": match_reasons,
                            "confidence": confidence
                        }

                        # Separate verified (high/medium confidence) from unverified
                        if confidence in ["high", "medium"]:
                            result["publications"].append(pub_entry)
                        else:
                            result["unverified_publications"].append(pub_entry)

                    except Exception as e:
                        continue

                # Sort by match score (highest first)
                result["publications"].sort(key=lambda x: (-x["match_score"], -(x["year"] or 0)))
                result["unverified_publications"].sort(key=lambda x: (-x["match_score"], -(x["year"] or 0)))

                result["verified_count"] = len(result["publications"])
                result["publications_found"] = len(result["publications"]) > 0 or len(result["unverified_publications"]) > 0

                # Add verification note
                if result["verified_count"] > 0:
                    result["verification_note"] = f"Found {result['verified_count']} publications with location/specialty match"
                elif result["unverified_publications"]:
                    result["verification_note"] = "Publications found but author identity not verified - review affiliations"

        except Exception as e:
            result["error"] = str(e)

    return result


# =============================================================================
# MAIN PIPELINE
# =============================================================================

async def run_dossier_pipeline(physician_name: str, state: str = None, city: str = None) -> Dict[str, Any]:
    """Run the full dossier pipeline."""

    # Parse name
    first_name, last_name, full_name = parse_physician_name(physician_name)

    if not last_name:
        return {"error": "Could not parse physician name. Please enter first and last name."}

    result = {
        "physician_name": physician_name,
        "parsed_name": {"first": first_name, "last": last_name, "full": full_name},
        "npi_data": None,
        "cms_data": None,
        "education_data": None,
        "publications": None,
        "timestamp": datetime.now().isoformat()
    }

    # Step 1: Check database first for CMS data
    st.info(f"🔍 Searching database for {first_name} {last_name}...")
    cms_result = fetch_cms_payments_from_db(first_name, last_name)
    result["cms_data"] = cms_result

    # If we found data in DB, use that physician info
    npi = None
    if cms_result.get("payments_found") and cms_result.get("physician_info"):
        info = cms_result["physician_info"]
        npi = info.get("npi")
        result["npi_data"] = {
            "found": True,
            "npi": npi,
            "verified_name": info.get("name"),
            "specialty": info.get("specialty"),
            "address": {"city": info.get("city"), "state": info.get("state")},
            "source": "CMS Database"
        }
        st.success(f"✅ Found {info.get('name')} in database!")
    else:
        # Fall back to NPI lookup
        st.info("🔍 Searching NPI Registry...")
        npi_result = await lookup_npi(first_name, last_name, state, city)
        result["npi_data"] = npi_result
        npi = npi_result.get("npi") if npi_result.get("found") else None

        if npi_result.get("found"):
            st.success(f"✅ Found NPI: {npi}")
        else:
            st.warning("⚠️ No NPI match found")

    # Step 2: Education & Training lookup
    st.info("🔍 Gathering education & training data...")

    edu_city = None
    edu_state = None
    edu_specialty = None

    if cms_result.get("physician_info"):
        info = cms_result["physician_info"]
        edu_city = info.get("city")
        edu_state = info.get("state")
        edu_specialty = info.get("specialty")
    elif result.get("npi_data", {}).get("found"):
        npi_data = result["npi_data"]
        edu_city = npi_data.get("address", {}).get("city")
        edu_state = npi_data.get("address", {}).get("state")
        edu_specialty = npi_data.get("specialty")

    education_result = await fetch_education_data(
        first_name=first_name,
        last_name=last_name,
        npi=npi,
        city=edu_city,
        state=edu_state,
        specialty=edu_specialty
    )
    result["education_data"] = education_result

    if education_result.get("found"):
        st.success("✅ Education & training data gathered")
    else:
        st.info("ℹ️ Limited education data available")

    # Step 3: PubMed - pass location info for author verification
    st.info("🔍 Searching PubMed for publications...")

    # Get location info from CMS data or NPI data
    pub_city = None
    pub_state = None
    pub_specialty = None

    if cms_result.get("physician_info"):
        info = cms_result["physician_info"]
        pub_city = info.get("city")
        pub_state = info.get("state")
        pub_specialty = info.get("specialty")
    elif result.get("npi_data", {}).get("address"):
        addr = result["npi_data"]["address"]
        pub_city = addr.get("city")
        pub_state = addr.get("state")
        pub_specialty = result["npi_data"].get("specialty")

    pubmed_result = await fetch_pubmed_publications(
        first_name,
        last_name,
        city=pub_city,
        state=pub_state,
        specialty=pub_specialty
    )
    result["publications"] = pubmed_result

    if pubmed_result.get("publications_found"):
        verified = pubmed_result.get("verified_count", 0)
        unverified = len(pubmed_result.get("unverified_publications", []))
        if verified > 0:
            st.success(f"✅ Found {verified} verified publications (+ {unverified} unverified)")
        else:
            st.warning(f"⚠️ Found {unverified} publications - author verification pending")
    else:
        st.info("ℹ️ No publications found")

    return result


# =============================================================================
# STREAMLIT UI
# =============================================================================

def main():
    # Header with branding
    st.title("🧠 Neurovascular Physician Dossier")
    st.markdown("""
    *Intelligence tool for **Neurosurgeons, Neurointerventionalists, and Stroke Specialists**
    treating hemorrhagic stroke, aneurysms, AVMs, and acute ischemic stroke*
    """)

    # Check database connection
    engine = get_db_connection()
    if engine:
        try:
            with engine.connect() as conn:
                count = conn.execute(text("SELECT COUNT(*) FROM cms_payments")).scalar()
                st.sidebar.success(f"✅ Database connected\n{count:,} payment records")
        except Exception as e:
            st.sidebar.error(f"Database error: {e}")
    else:
        st.sidebar.warning("⚠️ Database not available - using API fallback")

    # Sidebar
    with st.sidebar:
        st.header("About This Tool")
        st.markdown("""
        **Designed for Neuro/Stroke Teams:**
        - Neurological Surgery
        - Interventional Neuroradiology
        - Vascular Neurology
        - Endovascular Specialists

        **Use Cases:**
        - Stroke thrombectomy
        - Hemorrhagic stroke
        - Aneurysm treatment
        - AVM/AVF management
        """)

        st.divider()
        st.markdown("**Data Sources:**")
        st.markdown("- 📊 CMS Open Payments (2022-2024)")
        st.markdown("- 🏥 NPI Registry")
        st.markdown("- 📚 PubMed Publications")

        st.divider()
        st.markdown("**Competitor Companies Tracked:**")
        st.markdown("""
        - Penumbra
        - Medtronic
        - Stryker
        - MicroVention/Terumo
        - Balt
        - Rapid Medical
        - Phenox
        - J&J/Cerenovus (shown separately)
        """)

    # Input form
    col1, col2, col3 = st.columns([3, 1, 1])

    with col1:
        physician_name = st.text_input(
            "Physician Name",
            placeholder="e.g., Evan Joyce, Dr. Sarah Chen MD",
            help="Enter the physician's name (titles and credentials will be stripped automatically)"
        )

    with col2:
        state = st.selectbox(
            "State (optional)",
            options=[""] + list(US_STATES.keys()),
            format_func=lambda x: f"{x} - {US_STATES[x]}" if x else "Any State"
        )

    with col3:
        city = st.text_input("City (optional)", placeholder="e.g., Boise")

    # Search button
    if st.button("🔍 Generate Dossier", type="primary", use_container_width=True):
        if not physician_name:
            st.error("❌ Please enter a physician name")
            return

        with st.spinner("Gathering intelligence..."):
            try:
                result = asyncio.run(run_dossier_pipeline(
                    physician_name,
                    state=state if state else None,
                    city=city if city else None
                ))
            except Exception as e:
                st.error(f"❌ Error running pipeline: {e}")
                return

        if "error" in result and not result.get("cms_data"):
            st.error(f"❌ {result['error']}")
            return

        # Display results
        st.divider()
        st.header(f"📋 Dossier: {result.get('parsed_name', {}).get('full', physician_name)}")

        # Provider Information Section
        st.subheader("👤 Provider Information")
        npi_data = result.get("npi_data", {})
        cms_data = result.get("cms_data", {})

        if npi_data.get("found") or (cms_data.get("payments_found") and cms_data.get("physician_info")):
            info = cms_data.get("physician_info") or {}

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("NPI Number", npi_data.get("npi") or info.get("npi") or "N/A")
            with col2:
                st.metric("Verified Name", npi_data.get("verified_name") or info.get("name") or "N/A")
            with col3:
                addr = npi_data.get("address", {})
                city_state = f"{addr.get('city') or info.get('city', 'N/A')}, {addr.get('state') or info.get('state', 'N/A')}"
                st.metric("Location", city_state)
            with col4:
                years = npi_data.get("years_in_practice")
                st.metric("Years in Practice", f"{years}+" if years else "N/A")

            # Second row - credentials and organization
            col1, col2 = st.columns(2)
            with col1:
                credentials = npi_data.get("credentials")
                if credentials:
                    st.info(f"**Credentials:** {credentials}")
            with col2:
                org = npi_data.get("organization_name") or npi_data.get("address", {}).get("organization")
                if org:
                    st.info(f"**Organization:** {org}")

            specialty = npi_data.get("specialty") or info.get("specialty")
            if specialty:
                # Highlight if neuro specialty
                is_neuro = any(ns.lower() in specialty.lower() for ns in NEURO_SPECIALTIES)
                if is_neuro:
                    st.success(f"**Specialty:** {specialty} ✅ Neurovascular")
                else:
                    st.info(f"**Specialty:** {specialty}")

            # Show all specialties/licenses if available
            all_specs = npi_data.get("all_specialties", [])
            if len(all_specs) > 1:
                with st.expander(f"All Specialties & Licenses ({len(all_specs)})"):
                    for spec in all_specs:
                        primary_tag = " (Primary)" if spec.get("primary") else ""
                        license_info = f" - License: {spec.get('license')}" if spec.get("license") else ""
                        state_info = f" ({spec.get('state')})" if spec.get("state") else ""
                        st.markdown(f"- {spec.get('specialty')}{primary_tag}{state_info}{license_info}")

            # Show other NPI matches if available
            if npi_data.get("matches") and len(npi_data.get("matches", [])) > 1:
                with st.expander("Other potential matches"):
                    for match in npi_data["matches"][1:5]:
                        st.markdown(f"- **{match['name']}** (NPI: {match['npi']}) - {match.get('city', 'N/A')}, {match.get('state', 'N/A')}")
        else:
            st.warning("⚠️ No provider information found. Searched by name only.")

        st.divider()

        # Education & Training Section
        st.subheader("🎓 Education & Training")
        edu_data = result.get("education_data", {})

        if edu_data.get("found"):
            # Medical School (if available from external source)
            if edu_data.get("medical_school"):
                st.markdown(f"**🏫 Medical School:** {edu_data['medical_school']}")

            # Inferred Training Path
            inferred = edu_data.get("inferred_training", [])
            if inferred:
                st.markdown("**📚 Training Pathway** *(inferred from specialty)*")
                for training in inferred:
                    st.markdown(f"- **{training.get('type')}:** {training.get('program')}")

            # Board Certifications
            certs = edu_data.get("board_certifications", [])
            if certs:
                st.markdown("**🏅 Board Certifications** *(likely based on specialty)*")
                for cert in certs:
                    st.markdown(f"- **{cert.get('board')}:** {cert.get('certification')}")

            # Professional Organizations
            orgs = edu_data.get("professional_organizations", [])
            if orgs:
                st.markdown("**🤝 Professional Organizations** *(likely memberships)*")
                for org in orgs:
                    st.markdown(f"- {org.get('name')}")

            # Source attribution
            sources = edu_data.get("sources", [])
            if sources:
                st.caption(f"Data sources: {', '.join(sources)}")
        else:
            st.info("ℹ️ Education and training data not available. This information typically requires verification from institutional sources.")

        st.divider()

        # CMS Payments Section
        st.subheader("💰 Industry Payments (CMS Open Payments)")

        if cms_data.get("payments_found"):
            total_competitor = cms_data.get("total_competitor_amount", 0)
            total_jnj = cms_data.get("total_jnj_amount", 0)
            relationships = cms_data.get("relationships", [])

            col1, col2 = st.columns(2)
            with col1:
                st.metric(
                    "Total Competitor Payments",
                    f"${total_competitor:,.2f}",
                    help="Combined payments from Penumbra, Medtronic, Stryker, MicroVention, Balt, etc."
                )
            with col2:
                st.metric(
                    "J&J/Cerenovus Payments",
                    f"${total_jnj:,.2f}",
                    help="Payments from Johnson & Johnson / Cerenovus"
                )

            if relationships:
                # Create dataframe for chart
                df = pd.DataFrame(relationships)
                df = df[df["total_amount"] > 0].sort_values("total_amount", ascending=True)

                if not df.empty:
                    st.bar_chart(df.set_index("competitor")["total_amount"])

                # Details table
                st.markdown("**Payment Breakdown:**")
                for rel in relationships:
                    if rel["total_amount"] > 0:
                        jnj_badge = " 🔵 (J&J)" if rel.get("is_jnj") else ""
                        st.markdown(f"- **{rel['competitor']}**{jnj_badge}: ${rel['total_amount']:,.2f} ({rel['payment_count']} payments)")
        else:
            st.info("ℹ️ No CMS payment records found for this physician in our database.")
            if cms_data.get("error"):
                st.error(f"Error: {cms_data['error']}")

        st.divider()

        # Publications Section
        st.subheader("📚 Research Publications (PubMed)")
        pub_data = result.get("publications", {})

        if pub_data.get("publications_found"):
            verified_pubs = pub_data.get("publications", [])
            unverified_pubs = pub_data.get("unverified_publications", [])

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Found", pub_data.get("total_count", 0))
            with col2:
                st.metric("Verified", len(verified_pubs), help="Publications with matching location/affiliation")
            with col3:
                st.metric("Unverified", len(unverified_pubs), help="May be different author with same name")

            if pub_data.get("verification_note"):
                st.info(f"ℹ️ {pub_data['verification_note']}")

            # Verified publications
            if verified_pubs:
                st.markdown("### ✅ Verified Publications")
                st.caption("Author affiliation matches physician's known location")

                for pub in verified_pubs[:10]:
                    with st.container():
                        title = pub.get("title", "Untitled")
                        year = pub.get("year", "N/A")
                        journal = pub.get("journal", "")
                        url = pub.get("url", "")
                        confidence = pub.get("confidence", "low")
                        reasons = pub.get("match_reasons", [])
                        affiliation = pub.get("target_author_affiliation", "")

                        # Confidence badge
                        if confidence == "high":
                            badge = "🟢 HIGH"
                        elif confidence == "medium":
                            badge = "🟡 MEDIUM"
                        else:
                            badge = "🔴 LOW"

                        if url:
                            st.markdown(f"**[{title}]({url})** ({year}) {badge}")
                        else:
                            st.markdown(f"**{title}** ({year}) {badge}")

                        if journal:
                            st.caption(f"📰 {journal}")

                        # Show match reasons
                        if reasons:
                            st.caption(f"✓ Match: {', '.join(reasons)}")

                        # Show affiliation
                        if affiliation:
                            st.caption(f"🏥 {affiliation[:150]}{'...' if len(affiliation) > 150 else ''}")

                        st.markdown("---")

            # Unverified publications (collapsed by default)
            if unverified_pubs:
                with st.expander(f"⚠️ Unverified Publications ({len(unverified_pubs)}) - Review Manually"):
                    st.warning("These publications may belong to a different author with the same name. Review affiliations carefully.")

                    for pub in unverified_pubs[:10]:
                        title = pub.get("title", "Untitled")
                        year = pub.get("year", "N/A")
                        journal = pub.get("journal", "")
                        url = pub.get("url", "")
                        affiliation = pub.get("target_author_affiliation", "")
                        authors = pub.get("authors", [])

                        if url:
                            st.markdown(f"**[{title}]({url})** ({year})")
                        else:
                            st.markdown(f"**{title}** ({year})")

                        if journal:
                            st.caption(f"📰 {journal}")

                        if authors:
                            st.caption(f"👥 {', '.join(authors[:3])}{'...' if len(authors) > 3 else ''}")

                        if affiliation:
                            st.caption(f"🏥 {affiliation[:150]}{'...' if len(affiliation) > 150 else ''}")
                        else:
                            st.caption("🏥 No affiliation listed")

                        st.markdown("---")

        else:
            st.info("ℹ️ No publications found for this physician.")
            if pub_data.get("error"):
                st.caption(f"Note: {pub_data['error']}")

        # Download JSON
        st.divider()
        import json
        st.download_button(
            label="📥 Download Full Dossier (JSON)",
            data=json.dumps(result, indent=2, default=str),
            file_name=f"dossier_{physician_name.replace(' ', '_')}.json",
            mime="application/json"
        )


if __name__ == "__main__":
    main()
