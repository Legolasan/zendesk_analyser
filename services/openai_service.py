"""
Enhanced OpenAI Service for ticket analysis and test case generation.
"""
import os
import json
import re
from typing import Dict, Optional, List
from openai import OpenAI, OpenAIError


class EnhancedOpenAIService:
    """Enhanced OpenAI service for ticket analysis and test case generation."""
    
    def __init__(self, api_key: str, model: str = "gpt-5.1-2025-11-13"):
        self.client = OpenAI(api_key=api_key)
        self.model = model
    
    def analyze_ticket_phase1(self, conversation: str, timeout: int = 60) -> Dict:
        """
        Phase 1: Analyze ticket and extract issue description and root cause.
        
        Args:
            conversation: Zendesk ticket conversation text
            timeout: Timeout in seconds
            
        Returns:
            Dictionary with issue_description, root_cause, test_case_needed
        """
        prompt = f"""
You are a QA or software development engineer. Analyze this Zendesk ticket conversation and extract key information.

Ticket conversation:
---
{conversation}
---

Extract the following:
1. Issue Description (technical, as reported/observed)
2. Root Cause (precise, technical details if known/applicable)
   - If root cause cannot be identified from the conversation, write "Root cause not identified" or "Unable to determine root cause"
   - Only provide a root cause if you can identify the specific technical reason for the issue
   - Root cause must be CLEAR and SPECIFIC - vague or ambiguous root causes are not acceptable
3. Issue Theme: Identify the primary theme/category that BEST describes this specific issue.
4. Root Cause Theme: Identify the primary theme/category that BEST describes the root cause.
   CRITICAL: The theme MUST be derived directly from the issue description and root cause - it should closely match what the issue actually is, not force-fit into predefined categories.
   
   Theme Requirements:
   - Be a concise, descriptive label (2-4 words typically, but can be longer if needed for accuracy)
   - Capture the core nature/problem domain of the issue (e.g., what system component, what type of error, what operation)
   - Use terminology that matches the issue description itself - extract key terms from the issue
   - Evolve dynamically based on the specific issue - each issue may have a unique theme
   - Be specific and technical - avoid generic themes
   
   Theme Generation Strategy:
   - Look at the issue description: What is the main problem? What component/system is involved? What type of error/failure?
   - Combine these elements into a descriptive theme
   - Examples based on actual issues:
     * Issue: "NULL values not being replicated" → Theme: "NULL Value Replication"
     * Issue: "API calls failing due to rate limits" → Theme: "API Rate Limit Handling"
     * Issue: "Connection pool exhausted under load" → Theme: "Connection Pool Exhaustion"
     * Issue: "Data type mismatch causing validation errors" → Theme: "Data Type Validation Error"
     * Issue: "WAL logs not capturing TOAST data changes" → Theme: "WAL TOAST Data Capture"
     * Issue: "Authentication tokens expiring prematurely" → Theme: "Token Expiration Timing"
   
   - If the issue describes something unique or specific, create a theme that matches it exactly
   - The theme should help categorize similar issues together while being accurate to this specific problem
   - Avoid generic themes - always be specific to what the issue actually describes
4. Root Cause Theme: Identify the primary theme/category that BEST describes the root cause.
   CRITICAL: The root cause theme MUST be derived directly from the root cause description - it should closely match what the root cause actually is.
   
   Root Cause Theme Requirements:
   - Be a concise, descriptive label (2-4 words typically, but can be longer if needed for accuracy)
   - Capture the core technical cause/category (e.g., what type of bug, what system failure, what logic error)
   - Use terminology that matches the root cause description itself - extract key terms from the root cause
   - Evolve dynamically based on the specific root cause - each root cause may have a unique theme
   - Be specific and technical - avoid generic themes
   
   Root Cause Theme Generation Strategy:
   - Look at the root cause: What is the technical reason? What type of bug/failure? What component/system failed?
   - Combine these elements into a descriptive theme
   - Examples based on actual root causes:
     * Root Cause: "Missing permission check for column access" → Theme: "Permission Validation Missing"
     * Root Cause: "Timeout value too low for large data transfers" → Theme: "Timeout Configuration Mismatch"
     * Root Cause: "Connection pool not releasing connections properly" → Theme: "Connection Pool Leak"
     * Root Cause: "Data type conversion error during replication" → Theme: "Data Type Conversion Error"
     * Root Cause: "WAL log not capturing TOAST column changes" → Theme: "WAL TOAST Capture Missing"
     * Root Cause: "Rate limit exceeded without proper retry logic" → Theme: "Rate Limit Retry Logic Missing"
   
   - If root cause is "not identified" or unclear, use theme: "Root Cause Not Identified"
   - The root cause theme should help categorize similar root causes together
   - Avoid generic themes - always be specific to what the root cause actually describes
5. Test Case Needed: Answer "Yes" if a functional test case is needed, "No" if not needed.

CRITICAL RULES - Test Case Needed MUST be "No" if ANY of the following apply:

1. ROOT CAUSE NOT CLEAR:
   - Root cause is "not identified", "unable to determine", "unknown", or similar
   - Root cause is vague, ambiguous, or lacks specific technical details
   - Root cause cannot be clearly identified from the ticket conversation
   - Without a clear, specific root cause, we CANNOT create meaningful test cases
   - THIS IS THE PRIMARY REQUIREMENT: Root cause must be clear and specific

2. USER MISTAKES OR PRODUCT LIMITATIONS:
   - User configuration errors (wrong values set by user)
   - User misunderstanding of how system works
   - User not following documented procedures
   - Product limitations (system working as designed, documented constraints)
   - Missing features (system working as designed, feature doesn't exist)
   - Customer education/documentation issues
   - Issues resolved by user training or documentation updates

CRITICAL EVALUATION GUIDELINES:

Test cases SHOULD be created for ANY issue (functional OR non-functional) IF:
- Root cause is CLEAR and SPECIFIC
- Issue requires a code/logic fix or system improvement
- Issue could recur if similar conditions occur
- Issue can be validated through testing

This includes:
- Functional bugs (data processing errors, logic errors, API/integration issues)
- Non-functional issues (performance, scalability, security) - IF root cause is clear
- Connection handling problems (timeouts, retries, error recovery)
- Intermittent failures or "silently stops working" issues
- System failures that require code/logic fixes
- Issues where system doesn't handle edge cases properly
- Performance issues with identified root cause (memory leaks, inefficient queries, etc.)
- Scalability issues with identified root cause (bottlenecks, resource limits, etc.)

Test cases should NOT be created for:
- Pure configuration mistakes (someone set wrong value by accident) - USER MISTAKE
- One-time data corruption requiring manual fix - unless caused by functional bug
- Customer education/documentation issues - USER MISTAKE
- Feature gaps (system working as designed, missing features) - PRODUCT LIMITATION
- Issues resolved by granting permissions (not code bugs) - USER MISTAKE
- Product limitations or documented constraints - PRODUCT LIMITATION
- User errors or misunderstandings - USER MISTAKE
- Issues where root cause is NOT CLEAR or NOT IDENTIFIED

IMPORTANT DISTINCTION:
- "System doesn't handle X properly" → YES (if root cause clear, needs test)
- "Wrong config value was set by user" → NO (user mistake)
- "System is slow due to inefficient query" → YES (if root cause clear - performance issue with identified cause)
- "System crashes under load due to memory leak" → YES (if root cause clear - scalability issue with identified cause)
- "Feature doesn't exist" → NO (product limitation)
- "User didn't follow instructions" → NO (user mistake)
- "Connection errors causing failures" → YES (if root cause clear)
- "System is slow" → NO (root cause not clear - just symptom)

Examples:
- "Replication stops working due to timeout" → YES (if root cause clear - functional bug)
- "Wrong timeout value was configured by user" → NO (user mistake)
- "System is slow when processing large datasets due to missing index" → YES (if root cause clear - performance issue with identified cause)
- "System crashes under load due to memory leak in connection pool" → YES (if root cause clear - scalability issue with identified cause)
- "Feature X doesn't exist" → NO (product limitation)
- "Connection errors causing failures" → YES (if root cause clear - functional bug)
- "User didn't read documentation" → NO (user mistake)
- "System crashes when processing invalid data" → YES (if root cause clear - functional bug)
- "System is slow" → NO (root cause not clear - just symptom)

STRICT VALIDATION:
Before answering "Yes" for Test Case Needed, verify:
✓ Root cause is CLEAR and SPECIFIC (not vague or ambiguous) - THIS IS REQUIRED
✓ Issue is NOT a user mistake or product limitation
✓ Issue requires a code/logic fix or system improvement (not just user action or documentation)
✓ Issue can be validated through testing

NOTE: Both functional AND non-functional issues (performance, scalability, security) are acceptable IF root cause is clear.

Format output EXACTLY as follows:
Issue Description:
<your issue description>

Root Cause:
<your root cause - must be clear and specific, or "Root cause not identified">

Issue Theme:
<a concise, descriptive theme (2-4 words) that closely matches this specific issue - be accurate and specific, not generic>

Root Cause Theme:
<a concise, descriptive theme (2-4 words) that closely matches the root cause - be accurate and specific, not generic. If root cause is "not identified", use "Root Cause Not Identified">

Test Case Needed:
<Yes or No>
<brief reason - must explain why Yes/No based on the three critical rules above>
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.2,
                timeout=timeout,
            )
            
            output = response.choices[0].message.content
            
            # Parse response
            return self._parse_phase1_response(output)
        
        except OpenAIError as e:
            raise Exception(f"OpenAI API error in Phase 1: {str(e)}")
    
    def generate_test_case_with_solutions(
        self,
        ticket_analysis: Dict,
        search_results: Optional[Dict[str, List[Dict]]] = None,
        doc_check: Optional[Dict] = None,
        timeout: int = 90
    ) -> Dict:
        """
        Generate test case based on ticket analysis.
        
        Args:
            ticket_analysis: Results from Phase 1
            search_results: Optional search results (ignored, kept for backward compatibility)
            doc_check: Optional documentation check (ignored, kept for backward compatibility)
            timeout: Timeout in seconds
            
        Returns:
            Dictionary with test case information
        """
        prompt = f"""
You are a QA engineer creating comprehensive test cases based on ticket analysis.

TICKET ANALYSIS:
Issue Description: {ticket_analysis.get('issue_description', '')}
Root Cause: {ticket_analysis.get('root_cause', '')}

DECISION TREE: WHEN TO GENERATE TEST CASES

Step 1: Check if root cause is external (outside system's control)
- If root cause indicates external factors (source system load, network issues, third-party failures, infrastructure problems), proceed to EXTERNAL ROOT CAUSE HANDLING section below
- If root cause is internal or unclear, continue to Step 2

Step 2: Determine if test cases should be generated
Generate test cases if ALL of the following are true:
✓ At least ONE of:
  - Option A: Root cause is CLEAR and SPECIFIC (not vague or ambiguous), OR
  - Option B: Issue describes a FUNCTIONAL GAP or MISSING EXPECTED BEHAVIOR that can be validated
✓ Issue is NOT a user mistake or product limitation (user config error, product working as designed, missing feature, user didn't follow procedures)
✓ Issue requires a code/logic fix or system improvement (not just user action or documentation)
✓ Issue can be validated through testing

DO NOT generate test cases if:
- Root cause is "not identified", "unable to determine", "unknown", "unclear" AND issue does NOT describe missing expected behavior or functional gaps
- Root cause indicates user mistake or product limitation
→ In these cases, write "Number of Test Cases: 0" and skip all test case sections.

Note: Both functional AND non-functional issues (performance, scalability, security) are acceptable if they meet the above criteria.

TASK:
Analyze the ticket and identify ALL distinct test scenarios that need to be covered. A single ticket may reveal multiple bugs, edge cases, or failure modes that each need separate test cases.

TEST CASE QUANTITY:
Generate MULTIPLE test cases when:
- The ticket reveals different failure modes or bugs
- Multiple root causes or contributing factors exist
- Different components/systems are affected
- Edge cases or boundary conditions are identified
- Different aspects of the solution need validation

Generate ONE test case when:
- The issue is straightforward with a single root cause
- All aspects can be covered in one comprehensive test

GENERIC TEST CASE REQUIREMENTS:
Test cases MUST be GENERIC and REUSABLE - NOT instance-specific!

Each test case must:
1. Validate the PATTERN/CLASS of issue, not the specific instance that triggered it
2. Be GENERIC enough to catch ALL similar issues across different columns, tables, fields, or instances
3. Follow industry standards for this type of issue
4. Use generic terminology - avoid specific names/details from the ticket

Specific requirements:
- DO NOT use specific column names, table names, field names, or instance-specific identifiers from the ticket
- DO NOT reference exact values, IDs, or specific data from the ticket
- DO use generic placeholders:
  * "any column" or "all columns" instead of "column A" or "column_name"
  * "the table" or "any table" instead of specific table names
  * "any field" or "all fields" instead of specific field names
  * "any user" or "all users" instead of specific user IDs
  * "any permission" or "all permissions" instead of specific permission names
  * "any resource" instead of specific resource names

Examples:
❌ BAD (Instance-specific): "Test Case: Verify column 'customer_id' has proper permissions"
✅ GOOD (Generic): "Test Case: Verify all columns in the table have proper permissions"

❌ BAD: "Test column 'email' for NULL value handling"
✅ GOOD: "Test all columns for NULL value handling and validation"

EXTERNAL ROOT CAUSE HANDLING

IMPORTANT: For ALL root causes, first determine if it's external. If external, apply the rules below.

EXTERNAL ROOT CAUSE IDENTIFICATION:
A root cause is EXTERNAL if it involves factors outside the system's control. Look for patterns such as:
- Source system issues: "high load on source system", "source database overloaded", "source system timeout", "source system connection limit exceeded"
- Network issues: "network timeout", "connection dropped", "network instability", "latency issues"
- Third-party service failures: "external API failure", "third-party service unavailable", "upstream service error"
- Infrastructure problems: "infrastructure outage", "external dependency failure", "upstream system failure"
- Resource constraints in external systems: "source system resource exhaustion", "external system memory issues"

KEY PRINCIPLE: The system CANNOT prevent or control external failures. Test cases must validate HOW the system RESPONDS to external failures, not how it prevents them.

WHAT TO TEST (when root cause is external):
1. ERROR DETECTION: System correctly detects external failures (connection drops, timeouts, error responses)
2. ERROR HANDLING: System handles external failures gracefully without crashing or corrupting data
3. USER COMMUNICATION: System provides clear, actionable error messages that explain:
   - What happened (e.g., "Connection to source system failed")
   - Why it happened (e.g., "Source system is experiencing high load")
   - What the user can do (e.g., "Please retry after the source system load decreases, or contact your source system administrator")
4. RETRY LOGIC: System implements appropriate retry mechanisms with exponential backoff
5. TIMEOUT HANDLING: System respects timeout limits and fails gracefully when timeouts occur
6. GRACEFUL DEGRADATION: System degrades functionality gracefully rather than failing completely
7. CONNECTION RECOVERY: System attempts to recover connections when possible, with clear status updates

WHAT NOT TO TEST (when root cause is external):
❌ DO NOT create test cases that try to:
- Prevent external issues (e.g., "Ensure system maintains stable connection under high load")
- Control external systems (e.g., "Ensure source system handles load properly")
- Maintain stability when external system is unstable (e.g., "System should work normally when source is overloaded")
- Force external systems to behave correctly (e.g., "Ensure source system doesn't timeout")

EXAMPLES FOR EXTERNAL ROOT CAUSES:

Example 1: Source System High Load
Root Cause: "High load on source SQL Server causes connection to close prematurely"
❌ BAD: "Ensure that the system maintains a stable connection to the SQL Server even under high load conditions"
WHY BAD: System cannot control source system load - unrealistic and unachievable.
✅ GOOD: "When the source SQL Server is under high load and connection fails, verify that:
1. System detects the connection failure/timeout
2. System displays clear error message explaining the failure was due to high load on source system
3. System provides actionable guidance (retry after load decreases, contact administrator)
4. System logs error with context and allows user to retry"

Example 2: Network Timeout
Root Cause: "Network timeout causes data transfer to fail"
❌ BAD: "Ensure system maintains network connection even during network instability"
WHY BAD: System cannot control network stability.
✅ GOOD: "When network timeout occurs, verify that:
1. System detects timeout within configured period
2. System displays clear error: 'Network timeout occurred. Please check connection and retry.'
3. System implements retry logic with exponential backoff
4. System provides progress indication and allows cancel/retry"

Example 3: Third-Party API Failure
Root Cause: "External API service is unavailable"
❌ BAD: "Ensure external API service is always available"
WHY BAD: System cannot control third-party service availability.
✅ GOOD: "When external API service is unavailable, verify that:
1. System detects unavailability (HTTP 503, connection refused, etc.)
2. System displays user-friendly error with retry guidance
3. System implements retry logic with backoff
4. System logs error for monitoring"

OUTPUT FORMAT (all sections required):
Regression Test Needed:
<Yes or No or "N/A - Not applicable">
<brief reason>

Number of Test Cases:
<1, 2, 3, etc. - indicate how many distinct test cases are needed>

Test Case 1:
Title: <brief descriptive title - MUST be generic, not instance-specific>
Description: <GENERIC description - use "any/all" terminology, avoid specific names, focus on pattern validation>
Steps:
<step 1 - GENERIC: use "any column", "all columns", "any table", etc.>
<step 2 - GENERIC: avoid specific column/table/field names from ticket>
<step 3 - GENERIC: focus on pattern validation>
...
Regression Needed: <Yes/No for this specific test case>

Test Case 2: (only if multiple test cases needed)
Title: <brief descriptive title>
Description: <description>
Steps:
<step 1>
<step 2>
...
Regression Needed: <Yes/No>

Test Case 3: (only if needed)
...

Recommended Solution Approach:
<what approach should be taken to address the root cause? Write "N/A - No solution approach identified" if unclear>

Additional Test Scenarios:
<other scenarios to test based on similar issues, or "None identified" if no additional scenarios>
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2500,  # Increased for multiple test cases
                temperature=0.2,
                timeout=timeout,
            )
            
            output = response.choices[0].message.content
            
            # Parse response
            return self._parse_phase2_response(output, ticket_analysis, search_results)
        
        except OpenAIError as e:
            raise Exception(f"OpenAI API error in Phase 2: {str(e)}")
    
    def validate_test_cases(
        self,
        issue_description: str,
        root_cause: str,
        generated_test_cases: Dict,
        timeout: int = 60
    ) -> Dict:
        """
        Phase 3: Validate generated test cases against original issue and root cause.
        
        Args:
            issue_description: Original issue description from Phase 1
            root_cause: Original root cause from Phase 1
            generated_test_cases: Full Phase 2 results with test cases
            timeout: Timeout in seconds
            
        Returns:
            Dictionary with validation results, critical/minor issues, and optionally regenerated test cases
        """
        # Format test cases for validation prompt
        test_cases_text = ""
        test_cases_list = generated_test_cases.get('test_cases', [])
        if test_cases_list:
            for i, tc in enumerate(test_cases_list, 1):
                test_cases_text += f"\nTest Case {i}:\n"
                test_cases_text += f"Title: {tc.get('title', 'N/A')}\n"
                test_cases_text += f"Description: {tc.get('description', 'N/A')}\n"
                test_cases_text += f"Steps: {tc.get('steps', 'N/A')}\n"
        else:
            # Fallback to single test case format
            test_cases_text = f"\nTest Case:\n"
            test_cases_text += f"Description: {generated_test_cases.get('test_case_description', 'N/A')}\n"
            test_cases_text += f"Steps: {generated_test_cases.get('test_case_steps', 'N/A')}\n"
        
        # Check if root cause is external
        root_cause_lower = root_cause.lower() if root_cause else ''
        is_external = any(pattern in root_cause_lower for pattern in [
            'high load on source', 'source system', 'source database', 'source system timeout',
            'network timeout', 'connection dropped', 'network instability',
            'external api', 'third-party', 'upstream service', 'external service',
            'infrastructure outage', 'external dependency', 'upstream system'
        ])
        
        # Check if test cases are actually empty/N/A
        test_cases_empty = (
            test_cases_text.strip() == "" or
            "N/A" in test_cases_text.upper() or
            (not test_cases_list and 
             generated_test_cases.get('test_case_description', '').upper() in ['N/A', 'N/A - TEST CASE NOT NEEDED', ''])
        )
        
        if test_cases_empty:
            # Special handling for empty test cases
            prompt = f"""
You are a QA validation expert. Your task is to validate test case generation for the given issue and root cause.

ORIGINAL ISSUE:
{issue_description}

ORIGINAL ROOT CAUSE:
{root_cause}

GENERATED TEST CASES:
No test cases were generated. The test case description and steps are empty or marked as "N/A".

CRITICAL ISSUE DETECTED:
No test cases were generated for this issue, but a test case is needed based on the issue and root cause.

VALIDATION TASKS:
1. Confirm that test cases SHOULD be generated for this issue and root cause
2. Identify why test cases might not have been generated
3. Provide specific feedback on what test cases should be created

OUTPUT FORMAT:
Validation Passed: No
Overall Assessment: No test cases were generated. Test cases are required for this issue.

Critical Issues (if any):
- No test cases were generated despite test case being needed
- Test cases must be created to address: [describe what should be tested]

Minor Issues (if any):
None

Regeneration Needed: Yes
Regeneration Feedback:
Test cases need to be generated for this issue. The test cases should:
1. Address the specific issue: {issue_description[:200]}
2. Validate the root cause: {root_cause[:200]}
3. Be complete, technically correct, and testable
4. If root cause is external, focus on error handling and user communication (not prevention)
"""
        else:
            prompt = f"""
You are a QA validation expert. Your task is to validate whether the generated test cases are appropriate for the given issue and root cause.

ORIGINAL ISSUE:
{issue_description}

ORIGINAL ROOT CAUSE:
{root_cause}

GENERATED TEST CASES:
{test_cases_text}

VALIDATION TASKS:

1. RELEVANCE CHECK:
   - Do the test cases directly address the specific issue described?
   - Do the test cases align with the root cause identified?
   - Are the test cases testing the right thing based on the issue/root cause?

2. COMPLETENESS CHECK:
   - Do the test cases cover all important aspects of the issue?
   - Are there missing test scenarios that should be included?
   - Do the test cases address the full scope of the problem?

3. CORRECTNESS CHECK:
   - Is the test case logic sound and achievable?
   - Are the test steps realistic and testable?
   - Does the test case make technical sense?

4. EXTERNAL ROOT CAUSE HANDLING (if root cause is external):
   - Does the test case focus on ERROR HANDLING and USER COMMUNICATION?
   - Does it avoid trying to prevent or control external issues?
   - Does it validate how the system responds to external failures?
   - Does it include clear, actionable error messages?

CRITICAL ISSUES (require regeneration):
- Test cases do NOT address the issue or root cause
- Test cases are completely off-topic or irrelevant
- Test cases try to prevent external issues when root cause is external
- Test cases are technically impossible or unachievable
- Major gaps in coverage of the issue

MINOR ISSUES (flag only):
- Test cases could be more specific
- Minor improvements to test steps
- Additional edge cases could be tested
- Minor wording or clarity improvements

OUTPUT FORMAT:
Validation Passed: <Yes or No>
Overall Assessment: <brief summary of validation>

Critical Issues (if any):
<list each critical issue on a new line, or "None" if no critical issues>

Minor Issues (if any):
<list each minor issue on a new line, or "None" if no minor issues>

Regeneration Needed: <Yes or No>
Regeneration Feedback (if Regeneration Needed is Yes):
<specific feedback to guide regeneration, including what needs to be fixed>
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
                temperature=0.2,
                timeout=timeout,
            )
            
            output = response.choices[0].message.content
            
            # Parse validation response
            validation_results = self._parse_validation_response(output)
            
            # If critical issues found, regenerate test cases
            if validation_results.get('regeneration_needed', False):
                print("Critical issues found, regenerating test cases with feedback...")
                regeneration_feedback = validation_results.get('regeneration_feedback', '')
                
                # Create enhanced ticket analysis with validation feedback
                enhanced_ticket_analysis = {
                    'issue_description': issue_description,
                    'root_cause': root_cause,
                    'issue_theme': generated_test_cases.get('issue_theme', ''),
                    'root_cause_theme': generated_test_cases.get('root_cause_theme', ''),
                    'test_case_needed': True,
                    'test_case_needed_reason': f'Validation feedback: {regeneration_feedback[:200]}'
                }
                
                # Regenerate with feedback
                regeneration_prompt_addition = f"\n\nVALIDATION FEEDBACK FROM PREVIOUS GENERATION:\n{regeneration_feedback}\n\nIMPORTANT: Address the critical issues identified above when generating the test cases."
                
                regenerated_results = self._regenerate_test_cases_with_feedback(
                    enhanced_ticket_analysis,
                    regeneration_feedback,
                    timeout=timeout
                )
                
                validation_results['regenerated_test_cases'] = regenerated_results
            else:
                validation_results['regenerated_test_cases'] = None
            
            return validation_results
        
        except OpenAIError as e:
            raise Exception(f"OpenAI API error in Phase 3 validation: {str(e)}")
    
    def _regenerate_test_cases_with_feedback(
        self,
        ticket_analysis: Dict,
        feedback: str,
        timeout: int = 90
    ) -> Dict:
        """Regenerate test cases with validation feedback."""
        # Create enhanced ticket analysis with feedback appended to root cause context
        # This ensures the feedback is considered during regeneration
        enhanced_analysis = ticket_analysis.copy()
        
        # Append feedback to root cause to ensure it's considered in the prompt
        original_root_cause = ticket_analysis.get('root_cause', '')
        enhanced_analysis['root_cause'] = f"{original_root_cause}\n\n[VALIDATION FEEDBACK - CRITICAL ISSUES TO ADDRESS]:\n{feedback}\n\nIMPORTANT: The previous test case generation had critical issues. When generating new test cases, you MUST address the feedback above. Ensure test cases directly address the issue and root cause, are complete, technically correct, and if root cause is external, focus on error handling/communication (not prevention)."
        
        # Regenerate using the original method with enhanced context
        return self.generate_test_case_with_solutions(
            enhanced_analysis,
            search_results=None,
            doc_check=None,
            timeout=timeout
        )
    
    def _parse_validation_response(self, output: str) -> Dict:
        """Parse validation response from Phase 3."""
        def extract_section(key: str, text: str) -> str:
            parts = text.split(f'{key}:')
            if len(parts) > 1:
                # Find next section
                next_sections = [
                    'Validation Passed', 'Overall Assessment', 'Critical Issues',
                    'Minor Issues', 'Regeneration Needed', 'Regeneration Feedback'
                ]
                start = parts[1].strip()
                for section in next_sections:
                    if section != key and f'{section}:' in start:
                        start = start.split(f'{section}:')[0].strip()
                return start
            return ''
        
        validation_passed_text = extract_section('Validation Passed', output).strip()
        validation_passed = validation_passed_text.upper().startswith('YES')
        
        critical_issues_text = extract_section('Critical Issues', output).strip()
        critical_issues = []
        if critical_issues_text.upper() != 'NONE' and critical_issues_text:
            # Handle various formats: numbered lists, bullet points, plain text
            lines = [line.strip() for line in critical_issues_text.split('\n') if line.strip()]
            for line in lines:
                # Skip if it's just a section header or separator
                if any(skip in line.lower() for skip in ['critical issues', 'minor issues', '---', '===']):
                    continue
                # Remove bullet points, numbers, dashes
                cleaned = re.sub(r'^[-•*]\s*', '', line)  # Remove bullet
                cleaned = re.sub(r'^\d+[.)]\s*', '', cleaned)  # Remove numbered prefix
                cleaned = cleaned.strip()
                if cleaned and len(cleaned) > 5:  # Minimum length to be meaningful
                    critical_issues.append(cleaned)
        
        minor_issues_text = extract_section('Minor Issues', output).strip()
        minor_issues = []
        if minor_issues_text.upper() != 'NONE' and minor_issues_text:
            lines = [line.strip() for line in minor_issues_text.split('\n') if line.strip()]
            for line in lines:
                if any(skip in line.lower() for skip in ['critical issues', 'minor issues', '---', '===']):
                    continue
                cleaned = re.sub(r'^[-•*]\s*', '', line)
                cleaned = re.sub(r'^\d+[.)]\s*', '', cleaned)
                cleaned = cleaned.strip()
                if cleaned and len(cleaned) > 5:
                    minor_issues.append(cleaned)
        
        regeneration_needed_text = extract_section('Regeneration Needed', output).strip()
        regeneration_needed = regeneration_needed_text.upper().startswith('YES')
        
        regeneration_feedback = extract_section('Regeneration Feedback', output).strip() if regeneration_needed else ''
        
        # Ensure we have feedback if regeneration is needed
        if regeneration_needed and not regeneration_feedback:
            # Generate default feedback from critical issues
            if critical_issues:
                regeneration_feedback = "Critical issues found: " + "; ".join(critical_issues[:3])
            else:
                regeneration_feedback = "Test cases need to be regenerated to better address the issue and root cause."
        
        overall_assessment = extract_section('Overall Assessment', output).strip()
        if not overall_assessment:
            if validation_passed:
                overall_assessment = 'Validation passed'
            elif critical_issues:
                overall_assessment = f'Critical issues found: {len(critical_issues)} issue(s)'
            elif minor_issues:
                overall_assessment = f'Minor issues found: {len(minor_issues)} issue(s)'
            else:
                overall_assessment = 'Validation completed'
        
        return {
            'validation_passed': validation_passed,
            'overall_assessment': overall_assessment,
            'critical_issues': critical_issues,
            'minor_issues': minor_issues,
            'regeneration_needed': regeneration_needed,
            'regeneration_feedback': regeneration_feedback
        }
    
    def _parse_phase1_response(self, output: str) -> Dict:
        """Parse Phase 1 response."""
        def extract_section(key: str, text: str) -> str:
            parts = text.split(f'{key}:')
            if len(parts) > 1:
                # Find next section
                next_sections = ['Issue Description', 'Root Cause', 'Issue Theme', 'Root Cause Theme', 'Test Case Needed']
                start = parts[1].strip()
                for section in next_sections:
                    if section != key and f'{section}:' in start:
                        start = start.split(f'{section}:')[0].strip()
                return start
            return ''
        
        test_case_needed_text = extract_section('Test Case Needed', output).strip()
        test_case_needed = test_case_needed_text.upper().startswith('YES')
        
        return {
            'issue_description': extract_section('Issue Description', output),
            'root_cause': extract_section('Root Cause', output),
            'issue_theme': extract_section('Issue Theme', output).strip(),
            'root_cause_theme': extract_section('Root Cause Theme', output).strip(),
            'test_case_needed': test_case_needed,
            'test_case_needed_reason': test_case_needed_text
        }
    
    def _parse_phase2_response(
        self,
        output: str,
        ticket_analysis: Dict,
        search_results: Dict
    ) -> Dict:
        """Parse Phase 2 response, handling multiple test cases."""
        def extract_section(key: str, text: str, stop_at: List[str] = None) -> str:
            """Extract a section until the next section marker."""
            parts = text.split(f'{key}:')
            if len(parts) > 1:
                if stop_at is None:
                    stop_at = [
                        'Regression Test Needed', 'Number of Test Cases',
                        'Test Case 1', 'Test Case 2', 'Test Case 3', 'Test Case 4', 'Test Case 5',
                        'Test Case Description', 'Test Case Steps',
                        'Recommended Solution Approach', 'Additional Test Scenarios'
                    ]
                start = parts[1].strip()
                for section in stop_at:
                    if section != key and f'{section}:' in start:
                        start = start.split(f'{section}:')[0].strip()
                return start
            return ''
        
        def extract_test_case(number: int, text: str) -> Dict:
            """Extract a specific test case by number."""
            test_case_marker = f'Test Case {number}:'
            if test_case_marker not in text:
                return None
            
            # Find this test case section
            start_idx = text.find(test_case_marker)
            if start_idx == -1:
                return None
            
            # Find where next test case starts or end of test cases
            next_markers = [
                f'Test Case {number + 1}:',
                'Recommended Solution Approach:',
                'Additional Test Scenarios:'
            ]
            
            section_text = text[start_idx:]
            end_idx = len(section_text)
            for marker in next_markers:
                idx = section_text.find(marker)
                if idx != -1 and idx < end_idx:
                    end_idx = idx
            
            test_case_text = section_text[:end_idx]
            
            # Extract title, description, steps
            title = extract_section('Title', test_case_text, ['Description', 'Steps', 'Regression Needed']).strip()
            description = extract_section('Description', test_case_text, ['Steps', 'Regression Needed']).strip()
            steps = extract_section('Steps', test_case_text, ['Regression Needed', 'Title', 'Description']).strip()
            regression_text = extract_section('Regression Needed', test_case_text, ['Title', 'Description', 'Steps']).strip()
            regression_needed = regression_text.upper().startswith('YES') if regression_text else None
            
            return {
                'title': title,
                'description': description,
                'steps': steps,
                'regression_needed': regression_needed,
                'regression_reason': regression_text
            }
        
        regression_text = extract_section('Regression Test Needed', output).strip()
        regression_needed = None
        if 'N/A' not in regression_text.upper():
            regression_needed = regression_text.upper().startswith('YES')
        
        # Extract number of test cases
        num_test_cases_text = extract_section('Number of Test Cases', output).strip()
        num_test_cases = 1  # Default to 1
        try:
            # Try to extract number from text like "1", "2", "3 test cases", etc.
            match = re.search(r'(\d+)', num_test_cases_text)
            if match:
                num_test_cases = int(match.group(1))
        except (ValueError, AttributeError):
            pass
        
        # Extract all test cases
        test_cases = []
        for i in range(1, min(num_test_cases + 1, 6)):  # Limit to 5 test cases max
            test_case = extract_test_case(i, output)
            if test_case:
                test_cases.append(test_case)
        
        # If no test cases found with new format, try old format (backward compatibility)
        if not test_cases:
            test_case_desc = extract_section('Test Case Description', output)
            test_case_steps = extract_section('Test Case Steps', output)
            if test_case_desc or test_case_steps:
                test_cases.append({
                    'title': 'Test Case',
                    'description': test_case_desc,
                    'steps': test_case_steps,
                    'regression_needed': regression_needed,
                    'regression_reason': regression_text
                })
        
        # For backward compatibility, also set single test case fields
        primary_test_case = test_cases[0] if test_cases else {}
        
        return {
            'issue_description': ticket_analysis.get('issue_description', ''),
            'root_cause': ticket_analysis.get('root_cause', ''),
            'issue_theme': ticket_analysis.get('issue_theme', 'Unknown Theme'),
            'root_cause_theme': ticket_analysis.get('root_cause_theme', 'Unknown Root Cause Theme'),
            'test_case_needed': ticket_analysis.get('test_case_needed', False),
            'test_case_needed_reason': ticket_analysis.get('test_case_needed_reason', ''),
            'regression_test_needed': regression_needed,
            'regression_test_needed_reason': regression_text,
            # Multiple test cases (new format)
            'test_cases': test_cases,
            'num_test_cases': len(test_cases),
            # Backward compatibility (single test case fields)
            'test_case_description': primary_test_case.get('description', ''),
            'test_case_steps': primary_test_case.get('steps', ''),
            'recommended_solution': extract_section('Recommended Solution Approach', output),
            'additional_test_scenarios': extract_section('Additional Test Scenarios', output),
            'search_results_summary': ''
        }

