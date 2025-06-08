import sys
from uis.brake_control_system import Ui_Form
from PyQt5.QtWidgets import QMainWindow, QApplication


class BrakeControlSystemGUI(QMainWindow, Ui_Form):
    def __init__(self):
        super(BrakeControlSystemGUI, self).__init__()
        self.setupUi(self)
        self.showFullScreen()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = BrakeControlSystemGUI()
    window.show()
    sys.exit(app.exec_())
