Setup Guide
🪟 Windows
First Time
# Open project in VS Code → Terminal

# Create .env and add broker API credentials
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

python main.py
Next Runs
.venv\Scripts\Activate.ps1
python main.py

🍎 Mac
First Time
# Open project in VS Code → Terminal

# Create .env and add broker API credentials
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

python main.py
Next Runs
./start.sh
./stop.sh

☁️ Linux Cloud VM
First Time
# Amazon Linux
sudo dnf install -y python3.11 python3.11-pip python3.11-devel

# Create .env and add broker API credentials
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

python main.py
Next Runs
./start.sh
./stop.sh
Note: Install dependencies only during the first setup or when requirements.txt changes. `./stop.sh` asks the bot to exit and does not close open positions.
