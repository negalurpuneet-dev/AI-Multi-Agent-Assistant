# Multi-Agent AI Assistant

A student-focused chatbot with four task-specific assistants. A Flask backend
routes each message to the best-fit agent and asks Google Gemini to generate a
reply. The browser interface is built with plain HTML, CSS, and JavaScript.

## Features

- **Study Agent** for academic explanations, examples, and step-by-step help.
- **Coding Agent** for programming questions, debugging, and formatted code.
- **Writing Agent** for drafting and improving emails, reports, and summaries.
- **General Assistant Agent** for everyday questions and other requests.
- Rule-based routing with a general-purpose fallback; the selected agent is shown
  with each answer.
- Chat with Send or Enter; use Shift+Enter to add a line.
- Upload PNG, JPG, or WebP images and PDF, TXT, or Markdown documents from the
  sidebar; selected files are attached to the next message.
- Loading indicator, duplicate-send prevention, readable error messages, and
  copy-response buttons.
- New conversations, searchable history, and a confirmation before clearing the
  current conversation.
- Light and dark themes, responsive layout, quick prompts, and optional browser
  voice input.
- Conversation history and theme preference are saved in browser `localStorage`.
  They are local to that browser and are not stored on the server.
- Opening the site starts a fresh conversation; earlier chats remain available
  in History and can be reopened from there.
- Uploaded file contents are sent to Gemini only when the message is sent and
  are not saved by this application.

## Technology

- Python and Flask
- Google GenAI Python SDK (`google-genai`)
- Google Gemini API using `gemini-3.6-flash`, with a bounded fallback to
  `gemini-3.5-flash` for temporary upstream failures
- MathJax-rendered inline and display formulas for study answers
- `python-dotenv`
- HTML, CSS, and JavaScript

## Project structure

```text
AI_Chatbot/
├── backend/
│   ├── app.py
│   ├── assistant_agents.py
│   └── test_app.py
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── script.js
├── requirement/
│   └── requirements.txt
├── .gitignore
└── README.md
```

## Installation on Windows

Open PowerShell in this project folder:

```powershell
cd "C:\Users\Puneet\Documents\ai chatbot\AI_Chatbot"
```

If you do not already have the project virtual environment, create it:

```powershell
py -m venv venv
```

Install the packages into that environment:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirement\requirements.txt
```

## Gemini API key

The backend reads `GEMINI_API_KEY` from `backend/.env`. Create a key in
[Google AI Studio](https://aistudio.google.com/app/apikey) and put the actual
key after `GEMINI_API_KEY=`. Keep the key in that file only; never add it to
frontend code, screenshots, or source control. The root `.gitignore` excludes
this file. Restart Flask after changing the key.

If `backend/.env` does not exist, create it with:

```env
GEMINI_API_KEY=your_key_here
```

Do not overwrite an existing key just to follow this example. Create or manage
your own key in Google AI Studio. API access, quotas, and model availability are
controlled by Google and the account associated with the key.

## Run the application

From the project folder in PowerShell:

```powershell
.\venv\Scripts\python.exe backend\app.py
```

Open this address in a browser:

```text
http://127.0.0.1:5000
```

Keep the terminal running while using the site. Press Ctrl+C in that terminal
to stop Flask.

## How agent routing works

The Flask `/chat` endpoint validates the message, conversation history, and
optional image/document attachments, then
`backend/assistant_agents.py` counts task-specific keyword matches. The highest
matching category selects the Study, Coding, or Writing profile; if none match,
the General Assistant Agent is selected. Each profile adds specialist guidance
to shared answer-quality instructions, and the backend sends the message and
recent conversation context to Gemini. The selected model is
`gemini-3.6-flash`; after a transient server failure, the backend may make one
bounded request to `gemini-3.5-flash`. Invalid model, authentication, permission,
request, and quota errors are not retried. The SDK's automatic retries are
disabled so the app's single retry has a clear time limit. Provider retry
guidance is followed only when it fits within that limit.
The JSON response
contains both `reply` and `agent`, which the frontend uses to display the answer
and its source agent. Study answers explain calculations in plain language,
show substitutions and units, and use rendered formulas rather than raw LaTeX.

This is a rule-based router, not a group of autonomous agents. The agents use
the same Gemini model and do not independently collaborate or use external
tools.

Each `/chat` response includes a request ID. Backend logs include that ID, the
selected agent, HTTP status, error category, and request duration, but not the
message contents or API key. Match the browser's `request_id` or
`X-Request-ID` response header to the backend log line when investigating an
error. A `rate_or_quota_limit` response means Gemini returned HTTP 429; wait for
the quota window to reset or check the Google AI Studio project's limits and
billing.

## Run automated tests

The backend tests use Python's standard `unittest` library and mock Gemini calls,
so they do not use API quota:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s backend -p "test_*.py"
```

## Troubleshooting

- **PowerShell says the server is already in use:** stop the earlier Flask
  process with Ctrl+C, then run the command again.
- **API key is missing or rejected:** set a valid key from Google AI Studio as
  `GEMINI_API_KEY` in `backend/.env`, ensure the Gemini API is available to the
  associated project, and restart Flask. Never paste the key into chat or
  browser code.
- **Rate limit or quota message:** wait and retry, or check the quota for the
  Google AI Studio project.
- **Could not reach the server:** confirm Flask is running and open
  `http://127.0.0.1:5000` rather than opening `index.html` directly.
- **Voice input unavailable:** browser speech recognition support varies.
  Voice input is optional; typing and sending messages works independently.

## Current limitations

- Image uploads accept PNG, JPG, and WebP. Document uploads accept PDF, TXT,
  and Markdown. Files are sent to Gemini for analysis when you send a message.
- Voice input depends on browser Web Speech API support and may require
  microphone permission.
- Chat history is stored only in browser `localStorage`; clearing browser data
  removes it.
- Agent selection uses keyword matching and can choose the wrong agent for
  ambiguous prompts.
- Gemini requests require network access and a working API key. The application
  does not store chat history on the server.

## Possible future improvements

- Improve routing with a user-selectable agent or a lightweight classifier.
- Add optional server-side accounts and conversation storage.
- Expand automated browser testing and accessibility checks.

## Short project explanation

“This project is a Flask-based AI assistant with a web interface. When a user
sends a prompt, the backend checks its keywords and chooses a specialist profile
for studying, coding, writing, or general questions. It adds that agent's
instructions and sends the request to Google's Gemini API. The answer and agent
name are returned as JSON and shown in the browser. The API key stays in the
backend environment file, while chat history is saved locally in the browser.”
