"""
Field Mapper Utility for Priority Analyzer.
Maps Zendesk ticket custom field IDs to human-readable field names.
"""
import csv
import os
from typing import Dict, List, Optional


# Selected fields for Priority Analyzer (field_id -> display_name)
SELECTED_FIELDS = {
    "40860554056601": "Platform",
    "49138745436441": "Customer Name",
    "9774746026137": "Monthly Plan Tier",
    "50198956158617": "Deal Value (in ARR)",
    "53579979659417": "Impact to Hevo - Retention value",
    "53579969564697": "Impact to Hevo - Upsell potential",
    "47366530736921": "Fivetran parity",
    "49138927053465": "Urgency",
    "47498314998041": "Workaround available",
    "49139242724633": "Request Category",
    "47601744118553": "New Destination",
    "47601699917081": "New Source",
    "49277175956377": "Feature Request Title",
    "49276047881369": "Relevant Details"
}


def load_field_mapping(csv_path: Optional[str] = None) -> Dict[str, str]:
    """
    Load field ID to name mapping from CSV file.
    
    Args:
        csv_path: Path to ticket-fields.csv. If None, uses default location.
        
    Returns:
        Dictionary mapping field_id (str) -> field_name (str)
        Only includes the 14 selected fields for Priority Analyzer.
    """
    if csv_path is None:
        # Default to ticket-fields.csv in project root
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ticket-fields.csv')
    
    field_mapping = {}
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                field_id = row.get('Field ID', '').strip()
                display_name = row.get('Display name', '').strip()
                
                # Only include selected fields
                if field_id in SELECTED_FIELDS:
                    field_mapping[field_id] = display_name
    except FileNotFoundError:
        print(f"Warning: ticket-fields.csv not found at {csv_path}")
        # Fallback to hardcoded mapping
        field_mapping = SELECTED_FIELDS.copy()
    except Exception as e:
        print(f"Error loading field mapping from CSV: {str(e)}")
        # Fallback to hardcoded mapping
        field_mapping = SELECTED_FIELDS.copy()
    
    # Ensure all selected fields are in mapping (use hardcoded as fallback)
    for field_id, field_name in SELECTED_FIELDS.items():
        if field_id not in field_mapping:
            field_mapping[field_id] = field_name
    
    return field_mapping


def map_ticket_fields(custom_fields: List[Dict], field_mapping: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Map Zendesk API custom_fields array to a dictionary of field_name -> value.
    
    Args:
        custom_fields: List of dicts from Zendesk API: [{"id": 123, "value": "abc"}, ...]
        field_mapping: Optional pre-loaded mapping. If None, loads from CSV.
        
    Returns:
        Dictionary mapping field_name -> value
        Only includes fields that are in the selected fields list and have non-empty values.
    """
    if field_mapping is None:
        field_mapping = load_field_mapping()
    
    mapped_fields = {}
    
    for field in custom_fields:
        field_id = str(field.get('id', ''))
        field_value = field.get('value', '')
        
        # Skip empty values
        if not field_value or field_value == '' or field_value is None:
            continue
        
        # Map field ID to name if it's in our selected fields
        if field_id in field_mapping:
            field_name = field_mapping[field_id]
            mapped_fields[field_name] = str(field_value)
    
    return mapped_fields


def format_fields_for_prompt(ticket_fields: Dict[str, str]) -> str:
    """
    Format ticket fields as inline context for AI prompt.
    
    Args:
        ticket_fields: Dictionary of field_name -> value
        
    Returns:
        Formatted string to include in AI prompt
    """
    if not ticket_fields:
        return ""
    
    lines = ["TICKET METADATA:"]
    for field_name, field_value in sorted(ticket_fields.items()):
        # Truncate very long values for prompt
        value_str = str(field_value)
        if len(value_str) > 200:
            value_str = value_str[:200] + "..."
        lines.append(f"{field_name}: {value_str}")
    
    return "\n".join(lines) + "\n\n"


# Pre-load mapping at module level for efficiency
_field_mapping_cache = None

def get_field_mapping() -> Dict[str, str]:
    """Get cached field mapping (loads once, reuses)."""
    global _field_mapping_cache
    if _field_mapping_cache is None:
        _field_mapping_cache = load_field_mapping()
    return _field_mapping_cache
