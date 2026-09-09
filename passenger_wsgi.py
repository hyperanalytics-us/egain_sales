"""Entry point for cPanel "Setup Python App" (Phusion Passenger).

Passenger speaks WSGI and ESP is an ASGI app, so a2wsgi bridges the two.
This file must sit in the application root, beside the `esp/` package - which
is where it already lives in this repository.

cPanel setup (GoDaddy and most cPanel hosts):
  1. Software > Setup Python App > Create Application
       Python version    3.9 or newer
       Application root  esp            (a folder in your home dir, not public_html)
       Application URL   javasri.com/esp
       Startup file      passenger_wsgi.py
       Entry point       application
  2. Upload the repository into that application root.
  3. "Enter to the virtual environment" (cPanel shows the exact command),
     then:  pip install -r deploy/requirements-passenger.txt
     (or set that path as the app's "Configuration file" and click Run Pip Install)
  4. Add ANTHROPIC_API_KEY and ESP_PASSWORD in the app's Environment Variables.
  5. Restart the application.

Passenger sets SCRIPT_NAME=/esp, which a2wsgi turns into the ASGI root_path,
so ESP serves its assets and cookies under /esp without further configuration.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from a2wsgi import ASGIMiddleware  # noqa: E402

from esp.main import app  # noqa: E402

application = ASGIMiddleware(app)
