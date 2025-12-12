# Test Case Generation Improvement Proposal

## Executive Summary

The current test case generation produces generic, low-value test cases that don't capture the specific scenario or technical details from tickets. This proposal outlines improvements based on industry best practices ([Global App Testing](https://www.globalapptesting.com/blog/tips-to-write-functional-test-cases)) and analysis of real ticket scenarios (e.g., ticket #57238).

## Current Problems

### 1. **Lack of Specificity**
- **Current**: "Test data mapping in Hevo"
- **Should be**: "Validate PostgreSQL TOAST data handling when WAL omits unchanged large columns during backfill operations"
- **Impact**: Test cases are too generic and don't help QA engineers understand what to test

### 2. **Missing Technical Details**
- Root cause mentions specific technologies (TOAST, WAL, PostgreSQL) but test cases don't reference them
- Test cases lack technical context needed for execution
- **Impact**: Testers can't execute tests without additional research

### 3. **Incomplete Structure**
- Missing: Preconditions, Expected Results, Failure Criteria, Edge Cases
- Current structure is too minimal
- **Impact**: Test cases are incomplete and not actionable

### 4. **Not Scenario-Specific**
- Test steps don't replicate the exact scenario from the ticket
- Missing specific operations (backfill, incremental sync)
- Missing specific conditions (unchanged columns, WAL omission)
- **Impact**: Tests don't validate the actual bug scenario

### 5. **Weak Validation Criteria**
- Expected results are vague
- No specific validation queries or checks
- **Impact**: Can't determine if test passes or fails

## Proposed Solution

### Core Principles (from Global App Testing Article)

1. **Be Specific and Detailed**: Include technical details, specific scenarios
2. **Clear Structure**: Preconditions, Steps, Expected Results, Failure Criteria
3. **Actionable Steps**: Each step should be executable without ambiguity
4. **Comprehensive Coverage**: Include edge cases and negative scenarios
5. **Traceability**: Link test cases directly to root cause

### Proposed Test Case Structure

```
Title: <Specific title with technical details from root cause>
Description: <What is being validated and why, referencing root cause>
Preconditions: <Specific setup requirements>
Test Steps: <Detailed, actionable steps replicating ticket scenario>
Expected Results: <Specific, measurable validation criteria>
Failure Criteria: <What indicates test failure>
Edge Cases: <Additional scenarios to test>
Regression: <Yes/No with specific reason>
```

### Key Improvements

#### 1. Enhanced Phase 1: Issue Description & Root Cause

**Current**: Basic extraction
**Proposed**: 
- Require detailed technical root cause with specifics
- Include context: when/how issue occurs
- Reference specific systems, technologies, mechanisms
- Include technical details (e.g., TOAST, WAL, replication logic)

**Example Enhancement**:
- **Current**: "Issue is related to TOAST data handling"
- **Proposed**: "PostgreSQL TOAST data handling issue: When WAL omits TOAST data for unchanged large columns during backfill operations (where only other columns are updated), Hevo's replication logic incorrectly interprets absence as NULL and updates destination accordingly"

#### 2. Enhanced Phase 2: Test Case Generation Prompt

**Key Additions**:

**A. Specificity Requirements**:
```
CRITICAL: Test cases must:
- Match the EXACT scenario from the ticket
- Include ALL technical details from root cause
- Reference specific operations, systems, technologies mentioned
- Replicate the exact conditions that caused the issue
```

**B. Structure Requirements**:
```
Each test case MUST include:
1. Title: Specific, scenario-based with technical details
2. Description: What is validated and why (reference root cause)
3. Preconditions: Specific setup (systems, data, configurations)
4. Test Steps: Detailed, actionable steps replicating ticket scenario
5. Expected Results: Specific, measurable validation criteria
6. Failure Criteria: What indicates test failure
7. Edge Cases: Additional scenarios to test
8. Regression: Yes/No with specific reason
```

**C. Technical Detail Integration**:
```
- Must reference specific technologies/systems from root cause
- Include technical mechanisms (e.g., WAL, TOAST, replication logic)
- Reference specific operations (e.g., backfill, incremental sync)
- Include specific validation queries or checks
```

**D. Example-Based Learning**:
```
GOOD EXAMPLE:
Title: Validate PostgreSQL TOAST data handling when WAL omits unchanged large columns during backfill operations

Description: This test validates that when PostgreSQL WAL omits TOAST data for unchanged large columns during a backfill operation (where only other columns are updated), Hevo correctly fetches and replicates the TOAST data separately rather than incorrectly setting those columns to NULL in the destination.

Preconditions:
- PostgreSQL source database with table containing large column using TOAST storage
- Hevo pipeline configured with auto-mapping enabled
- Initial data synced with correct non-null values

Test Steps:
1. Insert test data: 100 rows with non-null COMMENT values (>2KB to trigger TOAST)
2. Perform backfill: Add new column, update ONLY new column (COMMENT unchanged)
3. Trigger incremental sync in Hevo pipeline
4. Verify: Query destination - COMMENT values match source exactly
5. Verify: Check logs for separate TOAST data fetch queries

Expected Results:
- COMMENT column values match source table exactly
- No COMMENT values incorrectly set to NULL
- Pipeline logs show TOAST data fetched separately

BAD EXAMPLE (too generic):
Title: Test data mapping
Description: Test data sync
Steps: Sync data and verify
```

#### 3. Enhanced Parsing Logic

**Current**: Basic extraction of title, description, steps
**Proposed**: 
- Parse all new sections (Preconditions, Expected Results, Failure Criteria, Edge Cases)
- Extract technical details and validation queries
- Structure data for better frontend display

#### 4. Frontend Display Updates

**Current**: Simple list display
**Proposed**:
- Structured display with all sections
- Highlight technical details
- Show validation criteria prominently
- Display edge cases separately

## Implementation Details

### Phase 1 Prompt Updates

```python
prompt = f"""
You are a QA or software development engineer. Analyze this Zendesk ticket conversation and extract key information.

CRITICAL: Extract DETAILED, TECHNICAL information. Include:
- Specific technologies, systems, or mechanisms mentioned
- Exact conditions or scenarios that caused the issue
- Technical context and specifics

Extract the following:
1. Issue Description (technical, detailed, as reported/observed)
   - Include specific systems, operations, conditions
   - Include when/how the issue occurs
   - Include impact and scale if mentioned

2. Root Cause (precise, technical details with specifics)
   - Include specific technologies/systems involved
   - Include technical mechanisms (e.g., WAL, TOAST, replication logic)
   - Include exact conditions that cause the issue
   - If root cause cannot be identified, write "Root cause not identified"
   - Only provide root cause if you can identify the specific technical reason

3. Test Case Needed: Answer "Yes" if a functional test case is needed, "No" if not needed.

CRITICAL RULE: If Root Cause is "not identified" or "unable to determine", then Test Case Needed MUST be "No".
"""
```

### Phase 2 Prompt Updates

```python
prompt = f"""
You are a QA engineer creating comprehensive, DETAILED test cases based on ticket analysis.

TICKET ANALYSIS:
Issue Description: {ticket_analysis.get('issue_description', '')}
Root Cause: {ticket_analysis.get('root_cause', '')}

RESEARCH RESULTS:
{formatted_search}

CRITICAL REQUIREMENTS FOR TEST CASES:

1. SPECIFICITY - Test cases MUST:
   - Include key technical details from root cause in title
   - Replicate the EXACT scenario from the ticket
   - Reference specific operations, systems, technologies mentioned
   - Match the exact conditions that caused the issue

2. STRUCTURE - Each test case MUST include ALL sections:
   - Title: Specific, scenario-based with technical details
   - Description: What is validated and why (reference root cause)
   - Preconditions: Specific setup requirements
   - Test Steps: Detailed, actionable steps
   - Expected Results: Specific, measurable validation criteria
   - Failure Criteria: What indicates test failure
   - Edge Cases: Additional scenarios to test
   - Regression: Yes/No with specific reason

3. TECHNICAL DETAILS - MUST include:
   - Specific technologies/systems from root cause
   - Technical mechanisms (e.g., WAL, TOAST, replication logic)
   - Specific operations (e.g., backfill, incremental sync)
   - Validation queries or checks

4. ACTIONABILITY - Steps must be:
   - Executable without ambiguity
   - Include specific values, queries, operations
   - Provide validation queries or checks

OUTPUT FORMAT:
[Detailed format with examples]
"""
```

## Optimizations

### 1. **Root Cause Detail Extraction**
- Require technical specifics in Phase 1
- Include context and conditions
- Reference specific systems/technologies

### 2. **Scenario Replication**
- Test steps must replicate exact ticket scenario
- Include specific operations mentioned
- Include specific conditions

### 3. **Technical Detail Integration**
- Reference technologies from root cause
- Include technical mechanisms
- Provide technical validation

### 4. **Structured Output**
- Complete test case structure
- All sections required
- Clear formatting

### 5. **Example-Based Learning**
- Include good/bad examples in prompt
- Show specificity requirements
- Demonstrate technical detail inclusion

## Expected Outcomes

### Quality Improvements
- ✅ Test cases match exact ticket scenarios
- ✅ Include technical specifics from root cause
- ✅ Steps are executable without ambiguity
- ✅ Complete structure with all sections
- ✅ Clear validation criteria

### Measurable Metrics
- Test cases reference specific technologies: **100%**
- Test steps replicate exact scenario: **100%**
- Validation criteria are specific: **100%**
- Test cases are executable: **100%**

## Implementation Steps

1. **Update Phase 1 Prompt** (Issue Description & Root Cause)
   - Require detailed technical extraction
   - Include context and specifics

2. **Update Phase 2 Prompt** (Test Case Generation)
   - Add specificity requirements
   - Add structure requirements
   - Include examples
   - Emphasize technical details

3. **Update Parsing Logic**
   - Parse new sections
   - Extract technical details
   - Structure data properly

4. **Update Frontend**
   - Display all sections
   - Highlight technical details
   - Format structured test cases

5. **Testing & Validation**
   - Test with ticket #57238
   - Compare before/after quality
   - Iterate based on results

## Success Criteria

- Test cases are specific and match ticket scenarios
- Technical details from root cause are included
- Test cases are actionable and executable
- Structure is complete with all required sections
- Quality matches industry best practices
