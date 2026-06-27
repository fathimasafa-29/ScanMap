# ScanMap - Placement Cell Automation

ScanMap is a stateless document processing web application designed to streamline administrative workflows for college placement cells. The system utilizes Optical Character Recognition (OCR) and document parsing engines to extract text from scanned files and automatically generate structured, professional Word documents for training reports and expenditure statements.

## 🚀 Features
* **Automated Data Extraction:** Processes scanned input files to extract text data cleanly without manual transcription.
* **Stateless Workflow:** Operates with zero persistent database storage—files are processed on-the-fly for real-time document delivery.
* **Professional Report Generation:** Converts raw data matches directly into cleanly structured Microsoft Word (.docx) files.
* **Clean Web Interface:** Simple frontend layout with optimized file upload interfaces.

## 🛠️ Tech Stack & Dependencies

### Core Frameworks
* **Frontend:** HTML5, CSS3, JavaScript
* **Backend:** Python (Flask)

### Required Python Packages
The application relies on the following package versions:
* `flask >= 3.0`
* `flask-cors >= 4.0`
* `pytesseract >= 0.3.10`
* `Pillow >= 10.0`
* `PyMuPDF >= 1.23`
* `reportlab >= 4.0`

---

## 📁 Project Structure
Project directory is organized like this:

```text
ScanMap/
│
├── backend/               # Python Flask logic
│   ├── app.py             # Main Flask server entry point
│   └── requirements.txt   # Mandatory Python library dependencies
│
├── frontend/              # Web user interface
│   ├── index.html         # Upload interface page
│   ├── script.js          # Client-side form handling
│   └── style.css          # Clean layout styling
│
└── README.md              # Project documentation and setup instructions