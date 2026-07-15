# Smart Motor Webpage!
**Try use the double motor and input/output!
**You can manually rotate the motor or use the slider bar!

Smart Motor Lab — Setup and Usage

Smart Motor Lab uses two files:

smart_motor_lab.html      Website interface
smart_motor_server.py     Local Python server and Bluetooth connection

The Python server must be running on the same computer that connects to the LEGO devices.

Before You Begin

You will need:

* A Mac or Windows computer
* Bluetooth enabled
* Python 3 installed
* The Smart Motor Lab repository downloaded
* The LEGO device’s color and four-digit serial number
* Chrome recommended

To check whether Python is installed, open Terminal or Command Prompt and run:

python --version

On some Macs, use:

python3 --version

You should see a Python version number.

⸻

Download the Project

Download ZIP and go to Smart-Motor-Lab, run the smart-motor-server.py

⸻

macOS Instructions

1. Open Terminal in the project folder

You should see:

smart_motor_lab.html
smart_motor_server.py


2. Create a virtual environment

Run:

python3 -m venv .venv

Activate it:

source .venv/bin/activate


3. Install the required packages

python3 -m pip install Flask Flask-SocketIO
python3 -m pip install legoeducation

If installation finishes without an error, continue to the next step.

4. Start the server

python3 smart_motor_server.py

Keep this Terminal window open.

5. Open the website

In Chrome, open:

http://localhost:5000

The top-right status should change from:

Demo mode

to:

Server connected

Do not open the HTML file by double-clicking it. Open http://localhost:5000 instead.

⸻
