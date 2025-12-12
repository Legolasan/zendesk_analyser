# Zendesk Ticket Summarizer

An AI-powered Flask web application that analyzes Zendesk tickets using Claude API (claude-3-5-sonnet) to generate comprehensive summaries, identify root causes, and automatically determine if test cases are needed.

## Features

- 🤖 **AI-Powered Analysis**: Uses OpenAI API (GPT-4o) to analyze ticket conversations
- 📝 **Automatic Summarization**: Generates issue descriptions and root cause analysis
- ✅ **Test Case Evaluation**: Intelligently determines if functional test cases are needed
- 🔄 **Regression Test Assessment**: Evaluates whether tests should be added to regression suite
- 🔍 **Solution Research**: Automatically searches web and Stack Overflow for solutions and best practices
- 💡 **Recommended Solutions**: Provides solution approaches based on research
- 📋 **Enhanced Test Cases**: Generates comprehensive test cases incorporating industry best practices
- 💾 **Data Persistence**: Stores all ticket summaries in SQLite database
- 🔍 **Search Functionality**: Search and retrieve previously analyzed tickets
- 🎨 **Modern UI**: Beautiful, responsive web interface with gradient styling

## Prerequisites

- Python 3.8 or higher
- OpenAI API key (required for ticket analysis)
- Zendesk API credentials (Basic Auth)
- Access to Zendesk instance (currently configured for `hevodata.zendesk.com`)

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Legolasan/zendesk_analyser.git
   cd zendesk_analyser
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   
   Create a `.env` file in the root directory:
   ```env
   OPENAI_API_KEY=your_openai_api_key_here  # Required for ticket analysis
   ZENDESK_AUTH=your_base64_encoded_zendesk_credentials
   SECRET_KEY=your_flask_secret_key_here
   PORT=5001
   ```
   
   **Note:** 
   - `OPENAI_API_KEY` is required for ticket analysis and test case generation
   
   **Zendesk Authentication:**
   - Format: `email/token:api_token` (for API token) or `email:password` (for password)
   - Encode to Base64: `echo -n "email:token" | base64`
   - Example: If your email is `user@example.com` and token is `abc123`, the auth string would be `dXNlckBleGFtcGxlLmNvbS90b2tlbjphYmMxMjM=`

5. **Initialize the database**
   
   The database will be automatically created on first run. The application uses SQLite with WAL (Write-Ahead Logging) mode for better performance.

## Usage

1. **Start the application**
   ```bash
   python app.py
   ```

2. **Access the web interface**
   
   Open your browser and navigate to:
   ```
   http://localhost:5001
   ```
   
   (Or the port specified in your `PORT` environment variable)

3. **Analyze a ticket**
   - Enter a Zendesk ticket ID in the form
   - Click "Analyze"
   - Wait for the AI to process the ticket (may take 30-60 seconds)
   - View the generated summary, root cause, and test case recommendations

4. **Search saved tickets**
   - Use the search box to find previously analyzed tickets
   - Browse recent tickets in the sidebar
   - Click "Load" on any ticket to view its full analysis

## Railway Deployment

This application is configured for deployment on Railway. Follow these steps to deploy:

### Prerequisites
- Railway account ([railway.app](https://railway.app))
- GitHub repository (already set up)
- All required API keys and credentials

### Deployment Steps

1. **Create Railway Project**
   - Go to [railway.app](https://railway.app)
   - Click "New Project"
   - Select "Deploy from GitHub repo"
   - Choose the `zendesk_analyser` repository

2. **Configure Environment Variables**
   
   Go to the "Variables" tab in your Railway project and add the following:
   
   | Variable | Description | Required |
   |----------|-------------|----------|
   | `OPENAI_API_KEY` | Your OpenAI API key | Yes |
   | `ZENDESK_AUTH` | Base64-encoded Zendesk credentials | Yes |
   | `SECRET_KEY` | Flask session secret key (generate with: `python -c "import secrets; print(secrets.token_hex(32))"`) | Yes |
   | `RAILWAY_ENVIRONMENT` | Set to `production` to disable debug mode | Recommended |
   | `PORT` | Automatically set by Railway | No (auto) |
   
   **Generate SECRET_KEY:**
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

3. **Deploy**
   - Railway will automatically detect the Flask application
   - It will use the `Procfile` to start the application with gunicorn
   - The application will be available at the Railway-provided URL

4. **Verify Deployment**
   - Check the Railway logs for any errors
   - Visit the provided Railway URL
   - Test by analyzing a sample ticket

### Railway-Specific Configuration

- **Procfile**: The application includes a `Procfile` that Railway uses to start the app:
  ```
  web: gunicorn app:app --bind 0.0.0.0:$PORT
  ```

- **Port Configuration**: The application automatically uses the `PORT` environment variable provided by Railway

- **Debug Mode**: Debug mode is automatically disabled when `RAILWAY_ENVIRONMENT=production` is set

- **Database**: The SQLite database will be created automatically on first run. For production, consider using Railway's PostgreSQL service for better reliability.

### Troubleshooting Railway Deployment

1. **Application won't start**
   - Check Railway logs for errors
   - Verify all required environment variables are set
   - Ensure `gunicorn` is in `requirements.txt`

2. **Port binding errors**
   - Verify the `Procfile` uses `$PORT` variable
   - Check that `app.py` uses `0.0.0.0` as host

3. **Environment variable issues**
   - Double-check all variables are set in Railway dashboard
   - Verify variable names match exactly (case-sensitive)

## API Endpoints

### Web Interface
- `GET /` - Main application interface
- `POST /` - Submit ticket ID for analysis

### REST API
- `GET /api/ticket/<ticket_id>` - Retrieve a specific ticket summary
- `GET /api/tickets/recent?limit=10` - Get recent ticket summaries
- `GET /api/tickets/search?q=<query>` - Search tickets by ID, issue, or root cause

## Project Structure

```
zendesk_ticket_summarizer/
├── app.py                 # Main Flask application
├── zendesk_auth.py        # Zendesk authentication handler
├── requirements.txt       # Python dependencies
├── .gitignore            # Git ignore rules
├── .env                  # Environment variables (not in repo)
├── ticket_summaries.db   # SQLite database (not in repo)
├── templates/
│   └── index.html        # Frontend template
└── IMPROVEMENTS.md       # Code improvement suggestions
```

## Database Schema

The application stores ticket summaries in a SQLite database with the following structure:

- `ticket_id` (TEXT, UNIQUE) - Zendesk ticket ID
- `issue_description` (TEXT) - AI-generated issue description
- `root_cause` (TEXT) - AI-identified root cause
- `test_case_needed` (INTEGER) - Boolean flag (0 or 1)
- `test_case_needed_reason` (TEXT) - Reasoning for test case decision
- `regression_test_needed` (INTEGER) - Boolean flag (0, 1, or NULL)
- `regression_test_needed_reason` (TEXT) - Reasoning for regression test decision
- `test_case_description` (TEXT) - Generic test case description
- `test_case_steps` (TEXT) - Detailed test case steps
- `created_at` (TIMESTAMP) - Creation timestamp
- `updated_at` (TIMESTAMP) - Last update timestamp

## Configuration

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `OPENAI_API_KEY` | Your OpenAI API key | Yes | - |
| `ZENDESK_AUTH` | Base64-encoded Zendesk credentials | Yes | - |
| `SECRET_KEY` | Flask session secret key | No | `dev-secret-key-change-in-production` |
| `PORT` | Server port | No | `5001` |
| `RAILWAY_ENVIRONMENT` | Set to `production` to disable debug mode | No | - |

### Zendesk URL

The application is currently configured for `hevodata.zendesk.com`. To change this, modify the `ZENDESK_URL_TEMPLATE` constant in `app.py`:

```python
ZENDESK_URL_TEMPLATE = "https://your-domain.zendesk.com/api/v2/tickets/{}/comments"
```

## How It Works

1. **Ticket Retrieval**: Fetches public comments from Zendesk API for the specified ticket ID
2. **Phase 1 - AI Analysis**: Analyzes the conversation to extract:
   - Issue description
   - Root cause
   - Test case needed decision (Yes/No)
3. **Phase 2 - Solution Research** (if test case needed):
   - Generates search queries from root cause
   - Searches web (via SerpAPI) and Stack Overflow for solutions
   - Finds best practices and similar resolved issues
4. **Phase 3 - Enhanced Test Case Generation**:
   - Generates test case incorporating research findings
   - Includes recommended solution approach
   - Adds additional test scenarios from similar issues
   - Provides solution-aware test steps
5. **Storage**: Saves the enhanced analysis to SQLite database
6. **Display**: Renders all results including research sources in the web interface

## Test Case Evaluation Logic

The AI uses sophisticated logic to determine if test cases are needed:

**Test cases ARE created for:**
- Functional bugs or defects
- Data processing errors
- Logic errors in code
- Edge cases or boundary conditions
- API or integration issues
- Business rule violations
- Security vulnerabilities
- Intermittent issues (indicates edge cases)

**Test cases are NOT created for:**
- Pure configuration errors
- One-time data corruption
- Customer education issues
- Infrastructure/deployment issues
- Feature gaps or missing functionality
- API version limitations

## Security Notes

⚠️ **Important Security Considerations:**

- Never commit `.env` file to version control
- Use strong `SECRET_KEY` in production
- Keep your OpenAI API key secure
- Rotate Zendesk credentials regularly
- The database file contains sensitive ticket information - ensure proper access controls

## Troubleshooting

### Common Issues

1. **"ZENDESK_AUTH is not set"**
   - Ensure your `.env` file exists and contains `ZENDESK_AUTH`
   - Verify the Base64 encoding is correct

2. **"OpenAI API error"**
   - Check your `OPENAI_API_KEY` is valid
   - Verify you have sufficient API credits
   - Check network connectivity

3. **"Zendesk API error: 401"**
   - Verify your Zendesk credentials are correct
   - Ensure your account has API access permissions

4. **Database errors**
   - Check file permissions for `ticket_summaries.db`
   - Ensure the directory is writable

## Future Improvements

See `IMPROVEMENTS.md` for a comprehensive list of suggested enhancements including:
- Security improvements
- Code quality enhancements
- Performance optimizations
- Testing infrastructure
- Documentation improvements

## License

This project is provided as-is for internal use.

## Contributing

This is a private project. For suggestions or improvements, please refer to the `IMPROVEMENTS.md` file.

## Support

For issues or questions, please check:
1. The troubleshooting section above
2. The `IMPROVEMENTS.md` file for known issues
3. GitHub Issues (if enabled)

---

**Built with:** Flask, OpenAI API (GPT-4o), SQLite, Bootstrap 5, Gunicorn

