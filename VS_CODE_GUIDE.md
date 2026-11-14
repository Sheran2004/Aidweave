# Run Aidweave in VS Code

## You’ll need

- Windows, macOS or Linux
- Python 3.11 or newer
- VS Code with the Microsoft Python extension

## Start the app

1. Extract `Aidweave-MVP.zip` to a folder you can edit.
2. In VS Code, choose **File → Open Folder** and open the extracted `Aidweave-MVP` folder.
3. Open **Terminal → New Terminal**.
4. Create a virtual environment and install the dependencies:

   **Windows PowerShell**

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   Copy-Item .env.example .env
   .\.venv\Scripts\python.exe server.py
   ```

   **macOS / Linux**

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   cp .env.example .env
   .venv/bin/python server.py
   ```

5. Open <http://localhost:8000> in your browser. Keep the terminal running while you use the site.

The first run creates a local SQLite database and three fictional example requests. The database file is `aidweave.db`; it is ignored by Git and is not included in the ZIP. The site adapts to phone screens and can be installed from supported mobile browsers; the install button appears when the browser offers installation. On iPhone, use Safari’s **Share → Add to Home Screen**. The app shell can open offline after its first visit, but account, board, messages, and posting need internet.

## Try the account flows

1. Create a personal account and use an email address you can recognize. The signup focus is only a preference: the same personal account can ask for help and volunteer. For local development, `EMAIL_PROVIDER=console` prints the verification link in the VS Code terminal instead of sending email. To test real delivery, configure a verified sender using `EMAIL_PROVIDER=resend` and `RESEND_API_KEY`, or `EMAIL_PROVIDER=smtp` and the SMTP settings in `.env`.
2. Open the verification link from the terminal output.
   If you already created an unverified account, click your name/account button on the site, choose **Resend verification email**, then open the fresh link printed in the VS Code terminal.
3. For a complete matching test, create a second test account (choose volunteering as its starting focus), verify it, and offer help on the first account’s request. A person cannot volunteer on their own request, but either account can ask for help or help someone else.
4. Sign in as the requester to try the private conversation and status tracking.
5. To try a private request, sign in as a requester, choose **One invited person**, and enter the test helper account’s email. The local invitation link is printed in the terminal. Open it while signed in to that account.
6. To test reports and NGO verification, create the first admin using the optional steps below.

Use only fictional information while testing. The website is a development build and is not connected to a live service.

## Optional: create a local admin

1. Stop the server with `Ctrl+C`.
2. Add a temporary random value for `BOOTSTRAP_ADMIN_TOKEN` in `.env`. Generate one in the terminal with:

   ```powershell
   py -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   On macOS/Linux, use `python3` instead of `py`.
3. Start the server again.
4. In a second VS Code terminal, set the same token and run the bootstrap script:

   **Windows PowerShell**

   ```powershell
   $env:AIDWEAVE_URL = 'http://localhost:8000'
   $env:BOOTSTRAP_ADMIN_TOKEN = 'paste-the-temporary-token-here'
   .\.venv\Scripts\python.exe scripts/bootstrap_admin.py
   ```

   **macOS / Linux**

   ```sh
   AIDWEAVE_URL=http://localhost:8000 BOOTSTRAP_ADMIN_TOKEN='paste-the-temporary-token-here' .venv/bin/python scripts/bootstrap_admin.py
   ```

5. Remove the token from `.env` after the first admin is created, then restart the server.

## Stop or reset local data

- Stop the server with `Ctrl+C`.
- To start fresh, stop the server and delete `aidweave.db`. The next start creates a new local database with fictional examples.

This app has not been acceptance-tested yet. Use the in-project `README.md` checklist after you start it, and send back any issue you find.
