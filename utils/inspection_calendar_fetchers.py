"""
Fetchers for scheduled building inspections from various public sources.
Supports: Contra Costa County, Berkeley, San Jose
"""

import logging
import requests
import pdfplumber
from datetime import datetime, date
from typing import List, Dict, Optional
from io import BytesIO
import pandas as pd

logger = logging.getLogger(__name__)


class ScheduledInspection:
    """Data class for a scheduled inspection"""

    def __init__(
        self,
        permit_id: str,
        address: str,
        inspection_date: date,
        inspection_type: str,
        jurisdiction: str,
        time_window_start: Optional[str] = None,
        time_window_end: Optional[str] = None,
        inspector_name: Optional[str] = None,
        source_url: Optional[str] = None,
    ):
        self.permit_id = permit_id
        self.address = address
        self.inspection_date = inspection_date
        self.inspection_type = inspection_type
        self.jurisdiction = jurisdiction
        self.time_window_start = time_window_start
        self.time_window_end = time_window_end
        self.inspector_name = inspector_name
        self.source_url = source_url
        self.fetched_at = datetime.now()

    def to_dict(self) -> Dict:
        """Convert to dictionary for database insertion"""
        return {
            'permit_id': self.permit_id,
            'address': self.address,
            'inspection_date': self.inspection_date,
            'inspection_type': self.inspection_type,
            'jurisdiction': self.jurisdiction,
            'time_window_start': self.time_window_start,
            'time_window_end': self.time_window_end,
            'inspector_name': self.inspector_name,
            'source_url': self.source_url,
            'fetched_at': self.fetched_at,
            'status': 'SCHEDULED',
            'gc_presence_probability': 0.8,  # Default high for inspection date
        }


class ScheduledInspectionFetcher:
    """Base class for inspection schedule fetchers"""

    def __init__(self):
        self.session = requests.Session()
        self.request_timeout = 30  # Timeout for HTTP requests (seconds)

    def fetch(self) -> List[ScheduledInspection]:
        """Fetch scheduled inspections. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement fetch()")

    def _parse_date(self, date_str: str, formats: List[str] = None) -> Optional[date]:
        """Helper to parse date strings"""
        if not date_str or not isinstance(date_str, str):
            return None

        if formats is None:
            formats = ['%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y', '%d/%m/%Y']

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue

        logger.warning(f"Could not parse date: {date_str}")
        return None


class ContraCostaFetcher(ScheduledInspectionFetcher):
    """Fetch scheduled inspections from Contra Costa County PDF"""

    PDF_URL = "https://www.contracosta.ca.gov/DocumentCenter/View/25242/InspectionSchedule"
    JURISDICTION = "contra_costa"

    def fetch(self) -> List[ScheduledInspection]:
        """Download and parse Contra Costa County inspection schedule PDF"""
        inspections = []

        try:
            logger.info(f"Fetching Contra Costa inspections from {self.PDF_URL}")
            response = self.session.get(self.PDF_URL, timeout=30)
            response.raise_for_status()

            # Parse PDF
            with pdfplumber.open(BytesIO(response.content)) as pdf:
                for page in pdf.pages:
                    # Extract table from page
                    tables = page.extract_tables()
                    if not tables:
                        continue

                    for table in tables:
                        inspections.extend(self._parse_table(table))

            logger.info(f"Found {len(inspections)} inspections in Contra Costa")
            return inspections

        except Exception as e:
            logger.error(f"Error fetching Contra Costa inspections: {e}")
            return []

    def _parse_table(self, table: List[List[str]]) -> List[ScheduledInspection]:
        """Parse a table from the PDF"""
        inspections = []

        # Skip header row
        for row in table[1:]:
            if len(row) < 4:
                continue

            try:
                # Typical format: [Permit#, Address, TimeWindow, Inspector]
                permit_id = str(row[0]).strip() if row[0] else None
                address = str(row[1]).strip() if row[1] else None
                time_window = str(row[2]).strip() if row[2] else None
                inspector = str(row[3]).strip() if row[3] else None

                if not permit_id or not address:
                    continue

                # Extract date and time window
                today = date.today()
                inspection_date = today  # PDF is for today

                # Try to parse time window (e.g., "9:00 AM - 12:00 PM")
                time_start = None
                time_end = None
                if time_window and '-' in time_window:
                    parts = time_window.split('-')
                    time_start = parts[0].strip() if len(parts) > 0 else None
                    time_end = parts[1].strip() if len(parts) > 1 else None

                inspection = ScheduledInspection(
                    permit_id=permit_id,
                    address=address,
                    inspection_date=inspection_date,
                    inspection_type="INSPECTION",  # Generic type
                    jurisdiction=self.JURISDICTION,
                    time_window_start=time_start,
                    time_window_end=time_end,
                    inspector_name=inspector if inspector and inspector != "" else None,
                    source_url=self.PDF_URL,
                )
                inspections.append(inspection)

            except Exception as e:
                logger.warning(f"Error parsing row: {row}, error: {e}")
                continue

        return inspections


class BerkeleyFetcher(ScheduledInspectionFetcher):
    """Fetch scheduled inspections from Berkeley PDF"""

    PDF_URL = "https://berkeleyca.gov/sites/default/files/documents/Scheduled%20Inspections%20Today.pdf"
    JURISDICTION = "berkeley"

    def fetch(self) -> List[ScheduledInspection]:
        """Download and parse Berkeley inspection schedule PDF"""
        inspections = []

        try:
            logger.info(f"Fetching Berkeley inspections from {self.PDF_URL}")
            response = self.session.get(self.PDF_URL, timeout=30)
            response.raise_for_status()

            # Parse PDF
            with pdfplumber.open(BytesIO(response.content)) as pdf:
                for page in pdf.pages:
                    # Extract table from page
                    tables = page.extract_tables()
                    if not tables:
                        continue

                    for table in tables:
                        inspections.extend(self._parse_table(table))

            logger.info(f"Found {len(inspections)} inspections in Berkeley")
            return inspections

        except Exception as e:
            logger.error(f"Error fetching Berkeley inspections: {e}")
            return []

    def _parse_table(self, table: List[List[str]]) -> List[ScheduledInspection]:
        """Parse a table from the PDF"""
        inspections = []

        # Skip header row
        for row in table[1:]:
            if len(row) < 4:
                continue

            try:
                # Typical format: [Permit#, Address, TimeWindow, Inspector]
                permit_id = str(row[0]).strip() if row[0] else None
                address = str(row[1]).strip() if row[1] else None
                time_window = str(row[2]).strip() if row[2] else None
                inspector = str(row[3]).strip() if row[3] else None

                if not permit_id or not address:
                    continue

                # PDF is for today
                today = date.today()
                inspection_date = today

                # Parse time window
                time_start = None
                time_end = None
                if time_window and '-' in time_window:
                    parts = time_window.split('-')
                    time_start = parts[0].strip() if len(parts) > 0 else None
                    time_end = parts[1].strip() if len(parts) > 1 else None

                inspection = ScheduledInspection(
                    permit_id=permit_id,
                    address=address,
                    inspection_date=inspection_date,
                    inspection_type="INSPECTION",
                    jurisdiction=self.JURISDICTION,
                    time_window_start=time_start,
                    time_window_end=time_end,
                    inspector_name=inspector if inspector and inspector != "" else None,
                    source_url=self.PDF_URL,
                )
                inspections.append(inspection)

            except Exception as e:
                logger.warning(f"Error parsing row: {row}, error: {e}")
                continue

        return inspections


class SanJoseFetcher(ScheduledInspectionFetcher):
    """Fetch inspection data from San Jose Open Data Portal (CKAN)"""

    API_URL = "https://data.sanjoseca.gov/api/3/action/datastore_search"
    DATASET_ID = "ca355e55-c651-4e00-9bde-2c014f229486"  # Building Permits Under Inspection
    JURISDICTION = "san_jose"

    def fetch(self) -> List[ScheduledInspection]:
        """Fetch from San Jose Open Data Portal"""
        inspections = []

        try:
            logger.info(f"Fetching San Jose inspections from CKAN API")

            params = {
                'resource_id': self.DATASET_ID,
                'limit': 1000,
            }

            response = self.session.get(self.API_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if not data.get('success'):
                logger.error(f"CKAN API returned error: {data.get('error')}")
                return []

            records = data.get('result', {}).get('records', [])

            for record in records:
                inspection = self._parse_record(record)
                if inspection:
                    inspections.append(inspection)

            logger.info(f"Found {len(inspections)} permits under inspection in San Jose")
            return inspections

        except Exception as e:
            logger.error(f"Error fetching San Jose inspections: {e}")
            return []

    def _parse_record(self, record: Dict) -> Optional[ScheduledInspection]:
        """Parse a CKAN record into ScheduledInspection"""
        try:
            # Field names may vary, try common variations
            permit_id = record.get('PERMIT_NUMBER') or record.get('permit_number') or record.get('PERMITID')
            address = record.get('LOCATION') or record.get('location') or record.get('ADDRESS')
            date_str = record.get('ISSUEDATE') or record.get('issue_date') or record.get('DATE')
            status = record.get('STATUS') or record.get('status')
            work_desc = record.get('WORKDESCRIPTION') or record.get('work_description')

            if not permit_id or not address:
                return None

            # Only include if under inspection
            if status and 'INSPECTION' not in status.upper() and 'UNDER' not in status.upper():
                return None

            # Parse date
            inspection_date = self._parse_date(date_str)
            if not inspection_date:
                # If no date, estimate next inspection (14 days from today)
                from datetime import timedelta
                inspection_date = date.today() + timedelta(days=14)

            # Guess inspection type from work description
            inspection_type = self._guess_inspection_type(work_desc)

            return ScheduledInspection(
                permit_id=str(permit_id),
                address=str(address),
                inspection_date=inspection_date,
                inspection_type=inspection_type,
                jurisdiction=self.JURISDICTION,
                time_window_start=None,  # Not available in CKAN data
                time_window_end=None,
                inspector_name=None,
                source_url=self.API_URL,
            )

        except Exception as e:
            logger.warning(f"Error parsing CKAN record: {e}")
            return None

    def _guess_inspection_type(self, work_desc: Optional[str]) -> str:
        """Guess inspection type from work description"""
        if not work_desc:
            return "INSPECTION"

        work_desc = str(work_desc).upper()

        keywords = {
            'FOUNDATION': ['FOUNDATION', 'FOOTING'],
            'FRAMING': ['FRAMING', 'FRAME', 'WOOD', 'STRUCTURAL'],
            'ROUGH_MEP': ['MEP', 'MECHANICAL', 'ELECTRICAL', 'PLUMBING', 'ROUGH'],
            'INSULATION': ['INSULATION', 'INSULATE'],
            'DRYWALL': ['DRYWALL', 'GYPSUM', 'SHEETROCK'],
            'FINAL': ['FINAL', 'COMPLETION', 'OCCUPANCY', 'CO'],
        }

        for insp_type, keywords_list in keywords.items():
            if any(kw in work_desc for kw in keywords_list):
                return insp_type

        return "INSPECTION"
