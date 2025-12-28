# Physician Dossier Lookup

A standalone Streamlit app that generates comprehensive physician intelligence from public data sources.

## Features

- **NPI Registry Lookup** - Find National Provider Identifier and verify physician identity
- **CMS Open Payments** - View industry payment relationships (2022-2024)
- **PubMed Publications** - Search research publications

## Data Sources

| Source | Description | URL |
|--------|-------------|-----|
| NPI Registry | National Provider Identifier database | npiregistry.cms.hhs.gov |
| CMS Open Payments | Industry payment disclosure data | openpaymentsdata.cms.gov |
| PubMed | Biomedical literature database | pubmed.ncbi.nlm.nih.gov |

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run app.py
```

## Deploy to Streamlit Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Deploy!

## Usage

1. Enter physician name (e.g., "Dr. John Smith, MD")
2. Optionally select a state to narrow results
3. Click "Generate Dossier"
4. View NPI info, industry payments, and publications
5. Download the full dossier as JSON

## License

Internal use only.
