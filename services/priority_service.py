"""
Priority Analyzer Service for Q1 Planning.
Analyzes Zendesk tickets to extract clear descriptions, themes, and priority signals.
"""
import os
import json
import re
from typing import Dict, List, Optional
from openai import OpenAI, OpenAIError
from utils.field_mapper import format_fields_for_prompt


# Predefined Hevo product areas for mapping
PRODUCT_AREAS = [
    "Connectors",      # Source connectors, data ingestion
    "Pipelines",       # Pipeline configuration, scheduling, monitoring
    "Destinations",    # Destination setup, data loading
    "Transforms",      # Data transformations, models
    "Activation",      # Activation/reverse ETL features
    "Platform",        # Authentication, billing, permissions, UI
    "Performance",     # Speed, latency, throughput issues
    "Other"            # Doesn't fit above categories
]


def extract_deal_value(ticket_fields: Optional[Dict[str, str]] = None, signal_details: str = "") -> Optional[str]:
    """
    Extract deal/ARR value from ticket fields or AI-generated signal_details text.
    
    Priority:
    1. Check ticket_fields for "Deal Value (in ARR)" field
    2. Extract from signal_details text using regex patterns
    
    Args:
        ticket_fields: Dictionary of ticket field_name -> value
        signal_details: AI-generated signal details text
        
    Returns:
        Extracted deal value as string (e.g., "5988"), or None if not found
    """
    # Priority 1: Check ticket_fields for "Deal Value (in ARR)"
    if ticket_fields:
        deal_value_field = ticket_fields.get("Deal Value (in ARR)")
        if deal_value_field and str(deal_value_field).strip():
            # Clean and return the value
            value = str(deal_value_field).strip()
            # Remove any currency symbols and commas for consistency
            value = value.replace('$', '').replace(',', '').strip()
            if value and value.replace('.', '').isdigit():
                return value
    
    # Priority 2: Extract from signal_details text using regex
    if signal_details:
        # Regex patterns to match various deal value formats
        patterns = [
            r'deal\s+value\s+(?:of\s+)?[\$]?(\d[\d,\.]*)',           # "deal value of 5988" or "deal value $5988"
            r'(\d[\d,\.]*)\s+(?:in\s+)?ARR',                          # "5988 in ARR" or "5988 ARR"
            r'ARR\s+(?:of\s+)?[\$]?(\d[\d,\.]*)',                     # "ARR of 5988" or "ARR $5988"
            r'\$(\d[\d,\.]*)\s+(?:in\s+)?ARR',                        # "$5988 in ARR"
            r'potential\s+(?:deal\s+)?value\s+(?:of\s+)?[\$]?(\d[\d,\.]*)',  # "potential value of 5988"
            r'revenue\s+(?:of|impact\s+of)?\s*[\$]?(\d[\d,\.]*)',    # "revenue of 5988" or "revenue impact of 5988"
            r'(\d[\d,\.]*)\s+(?:in\s+)?annual\s+revenue',            # "5988 in annual revenue"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, signal_details, re.IGNORECASE)
            if match:
                value = match.group(1).replace(',', '')
                # Validate it's a reasonable number (at least 3 digits for ARR)
                if value and len(value.replace('.', '')) >= 3:
                    return value
    
    return None


def format_deal_value_display(deal_value: str) -> str:
    """
    Format deal value for display with currency formatting.
    
    Args:
        deal_value: Raw deal value string (e.g., "5988" or "5988.50")
        
    Returns:
        Formatted string (e.g., "$5,988 ARR")
    """
    if not deal_value:
        return ""
    
    try:
        # Convert to float for formatting
        value = float(deal_value.replace(',', ''))
        # Format with comma separators, no decimal if whole number
        if value == int(value):
            formatted = f"${int(value):,}"
        else:
            formatted = f"${value:,.2f}"
        return f"{formatted} ARR"
    except (ValueError, TypeError):
        return f"${deal_value} ARR"


class PriorityAnalyzerService:
    """Service for analyzing ticket priority and extracting planning-relevant information."""
    
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = OpenAI(api_key=api_key)
        self.model = model
    
    def analyze_ticket_priority(self, conversation: str, ticket_fields: Optional[Dict[str, str]] = None, timeout: int = 60) -> Dict:
        """
        Analyze a ticket conversation to extract priority-relevant information for Q1 planning.
        
        Args:
            conversation: The full ticket conversation text with [CUSTOMER]/[AGENT] labels
            ticket_fields: Optional dictionary of ticket field_name -> value for context
            timeout: Timeout in seconds
            
        Returns:
            Dictionary with:
            - clear_description: Concise summary of the issue
            - ai_theme: Dynamic AI-generated theme
            - product_area: Mapped Hevo product area
            - is_blocker: Whether this is blocking the customer
            - is_churn_risk: Whether there's churn risk mentioned
            - is_escalation: Whether this was escalated
            - is_revenue_impact: Whether there's revenue/enterprise impact
            - is_lost_deal: Whether a lost deal or closed lost deal was mentioned
            - signal_details: Details about detected priority signals
            - priority_score: Overall priority (Critical/High/Medium/Low)
        """
        # Format ticket fields as context if provided
        fields_context = ""
        if ticket_fields:
            fields_context = format_fields_for_prompt(ticket_fields)
        
        prompt = f"""
You are analyzing a Zendesk support ticket to help with Q1 planning prioritization.

{fields_context}CONVERSATION FORMAT:
- [CUSTOMER]: Messages from the customer who reported the issue
- [AGENT]: Public responses from support agents
- [AGENT - INTERNAL]: Internal notes (engineering discussions, root cause analysis)

Ticket conversation:
---
{conversation}
---

Analyze this ticket and extract the following information:

1. CLEAR DESCRIPTION:
   - Write a clear, concise summary of what the customer's actual problem is
   - Focus on the business impact, not just technical details
   - Should be understandable by non-technical stakeholders
   - 2-4 sentences maximum

2. AI THEME:
   - Generate a descriptive theme that captures the essence of this issue
   - Should be specific and actionable (e.g., "BigQuery Authentication Timeout", "Salesforce Connector Rate Limiting")
   - 2-5 words typically

3. PRODUCT AREA:
   - Map to ONE of these Hevo product areas:
     * Connectors - Source connectors, data ingestion issues
     * Pipelines - Pipeline configuration, scheduling, monitoring
     * Destinations - Destination setup, data loading issues
     * Transforms - Data transformations, models
     * Activation - Activation/reverse ETL features
     * Platform - Authentication, billing, permissions, UI issues
     * Performance - Speed, latency, throughput issues
     * Other - Doesn't fit above categories

4. PRIORITY SIGNALS - Detect if any of these are present in the conversation:

   a) BLOCKER: Is this blocking the customer's work?
      Look for: "blocking", "blocker", "can't proceed", "stuck", "urgent", "critical", 
      "production blocked", "waiting on this", "deadline", "can't continue"
   
   b) CHURN RISK: Is there risk of customer leaving?
      Look for: "churn", "cancel", "leaving", "competitor", "switching", "evaluating alternatives",
      "disappointed", "frustrated", "losing trust", "reconsider", "not renewing"
   
   c) ESCALATION: Has this been escalated or is management involved?
      Look for: "manager", "VP", "escalate", "executive", "leadership", "CEO", "CTO",
      "escalated", "priority ticket", "urgent attention", "management"
   
   d) REVENUE IMPACT: Is there significant revenue/business impact?
      Look for: "enterprise", "large customer", "deal", "renewal", "revenue", "contract",
      "POC", "pilot", "evaluation", "big account", "strategic customer", "key account"
   
   e) LOST DEAL: Was a deal lost or closed lost mentioned?
      Look for: "closed lost deal", "lost deal", "deal lost", "closed lost", 
      "deal closed", "lost opportunity", "missed deal", "deal didn't close"

5. PRIORITY SCORE:
   Calculate based on signals detected:
   - Critical: 3+ signals detected OR explicit production down/data loss
   - High: 2 signals detected OR blocker mentioned
   - Medium: 1 signal detected
   - Low: No signals detected

Format your response EXACTLY as follows:

Clear Description:
<your clear description here>

AI Theme:
<your theme here>

Product Area:
<one of: Connectors, Pipelines, Destinations, Transforms, Activation, Platform, Performance, Other>

Is Blocker:
<Yes or No>
<brief evidence if Yes, or "No blocking language detected" if No>

Is Churn Risk:
<Yes or No>
<brief evidence if Yes, or "No churn indicators detected" if No>

Is Escalation:
<Yes or No>
<brief evidence if Yes, or "No escalation indicators detected" if No>

Is Revenue Impact:
<Yes or No>
<brief evidence if Yes, or "No revenue impact indicators detected" if No>

Is Lost Deal:
<Yes or No>
<brief evidence if Yes, or "No lost deal indicators detected" if No>

Priority Score:
<Critical, High, Medium, or Low>
<brief justification>
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
                temperature=0.2,
                timeout=timeout,
            )
            
            output = response.choices[0].message.content
            return self._parse_priority_response(output)
        
        except OpenAIError as e:
            raise Exception(f"OpenAI API error in priority analysis: {str(e)}")
    
    def _parse_priority_response(self, output: str) -> Dict:
        """Parse the AI response into structured data."""
        
        def extract_section(key: str, text: str, next_sections: List[str] = None) -> str:
            """Extract content between a section header and the next section."""
            parts = text.split(f'{key}:')
            if len(parts) > 1:
                if next_sections is None:
                    next_sections = [
                        'Clear Description', 'AI Theme', 'Product Area',
                        'Is Blocker', 'Is Churn Risk', 'Is Escalation',
                        'Is Revenue Impact', 'Is Lost Deal', 'Priority Score'
                    ]
                content = parts[1].strip()
                for section in next_sections:
                    if section != key and f'{section}:' in content:
                        content = content.split(f'{section}:')[0].strip()
                return content
            return ''
        
        def parse_yes_no_with_details(section_text: str) -> tuple:
            """Parse a Yes/No section and extract details."""
            lines = section_text.strip().split('\n')
            if not lines:
                return False, ''
            
            is_yes = lines[0].strip().upper().startswith('YES')
            details = '\n'.join(lines[1:]).strip() if len(lines) > 1 else ''
            return is_yes, details
        
        # Extract each section
        clear_description = extract_section('Clear Description', output)
        ai_theme = extract_section('AI Theme', output).strip()
        product_area = extract_section('Product Area', output).strip()
        
        # Validate product area
        if product_area not in PRODUCT_AREAS:
            # Try to find a close match
            product_area_lower = product_area.lower()
            matched = False
            for area in PRODUCT_AREAS:
                if area.lower() in product_area_lower or product_area_lower in area.lower():
                    product_area = area
                    matched = True
                    break
            if not matched:
                product_area = "Other"
        
        # Parse priority signals
        blocker_text = extract_section('Is Blocker', output)
        is_blocker, blocker_details = parse_yes_no_with_details(blocker_text)
        
        churn_text = extract_section('Is Churn Risk', output)
        is_churn_risk, churn_details = parse_yes_no_with_details(churn_text)
        
        escalation_text = extract_section('Is Escalation', output)
        is_escalation, escalation_details = parse_yes_no_with_details(escalation_text)
        
        revenue_text = extract_section('Is Revenue Impact', output)
        is_revenue_impact, revenue_details = parse_yes_no_with_details(revenue_text)
        
        lost_deal_text = extract_section('Is Lost Deal', output)
        is_lost_deal, lost_deal_details = parse_yes_no_with_details(lost_deal_text)
        
        # Parse priority score
        priority_text = extract_section('Priority Score', output)
        priority_lines = priority_text.strip().split('\n')
        priority_score = 'Medium'  # Default
        priority_justification = ''
        
        if priority_lines:
            first_line = priority_lines[0].strip().lower()
            if 'critical' in first_line:
                priority_score = 'Critical'
            elif 'high' in first_line:
                priority_score = 'High'
            elif 'low' in first_line:
                priority_score = 'Low'
            else:
                priority_score = 'Medium'
            
            priority_justification = '\n'.join(priority_lines[1:]).strip() if len(priority_lines) > 1 else ''
        
        # Compile signal details
        signal_details_parts = []
        if is_blocker and blocker_details:
            signal_details_parts.append(f"Blocker: {blocker_details}")
        if is_churn_risk and churn_details:
            signal_details_parts.append(f"Churn Risk: {churn_details}")
        if is_escalation and escalation_details:
            signal_details_parts.append(f"Escalation: {escalation_details}")
        if is_revenue_impact and revenue_details:
            signal_details_parts.append(f"Revenue Impact: {revenue_details}")
        if is_lost_deal and lost_deal_details:
            signal_details_parts.append(f"Lost Deal: {lost_deal_details}")
        if priority_justification:
            signal_details_parts.append(f"Priority: {priority_justification}")
        
        signal_details = ' | '.join(signal_details_parts) if signal_details_parts else ''
        
        return {
            'clear_description': clear_description,
            'ai_theme': ai_theme,
            'product_area': product_area,
            'is_blocker': is_blocker,
            'is_churn_risk': is_churn_risk,
            'is_escalation': is_escalation,
            'is_revenue_impact': is_revenue_impact,
            'is_lost_deal': is_lost_deal,
            'signal_details': signal_details,
            'priority_score': priority_score
        }
