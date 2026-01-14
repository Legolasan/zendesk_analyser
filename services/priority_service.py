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
                        'Is Revenue Impact', 'Priority Score'
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
            'signal_details': signal_details,
            'priority_score': priority_score
        }
