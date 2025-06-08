import os

if __name__ == "__main__":
    os.system('pyinstaller --onefile --clean --windowed --icon="./uis/images/icon.ico" --dist="./" main.py')