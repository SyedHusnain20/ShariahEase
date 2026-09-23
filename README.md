# ShariahEase

**ShariahEase** is a web application that brings Islamic finance tools and Shariah-compliant guidance into one place — combining practical calculators, halal investment screening, and an AI-powered advisory assistant.

---

## ✨ Features

- **Zakat & Finance Calculators** — Calculate Zakat obligations (based on nisab thresholds) and run other Islamic finance calculations relevant to personal or business assets.
- **Halal Investment Screening & Tracking** — Check and track investments against Shariah-compliance criteria.
- **AI-Powered Advisory (RAG)** — Ask questions and get Shariah-informed answers, powered by a Retrieval-Augmented Generation pipeline backed by a FAISS vector store instead of a traditional database.
- **Interactive Dashboards** — Visual breakdowns of finances and investments using Chart.js.
- **Fully Responsive UI** — Optimized for mobile and desktop, including:
  - Mobile bottom navigation bar
  - Hamburger sidebar overlay on smaller screens
  - Safe-area inset support for notched/modern devices

---

## 🛠 Tech Stack

| Layer         | Technology                          |
|---------------|--------------------------------------|
| Backend       | Python, Jinja2 (server-rendered templates) |
| Frontend      | Tailwind CSS, Alpine.js, Chart.js    |
| AI / Retrieval| FAISS (vector store) for RAG-based Q&A |
| LLM           | Groq — Llama 3.3 70B                 |
| Data storage  | No traditional relational database — knowledge is stored and retrieved via FAISS embeddings |

---

## 📂 Project Structure

```
ShariahEase/
├── app/                # Application source code
│   ├── templates/      # Jinja2 templates
│   ├── static/         # CSS, JS, images
│   ├── routes/         # Route handlers / views
│   └── rag/            # RAG pipeline (embeddings, FAISS index, retrieval logic)
├── data/                # Source documents used to build the FAISS index
├── requirements.txt
└── README.md
```

*(Adjust this tree to match your actual folder layout.)*

---

## 🚀 Getting Started

### Prerequisites
- Python 3.x
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/<your-username>/ShariahEase.git
cd ShariahEase

# Create a virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the project root with any required keys, for example:

```
GROQ_API_KEY=your_key_here
FAISS_INDEX_PATH=data/faiss_index
```

### Running Locally

```bash
python app.py
```

The app will be available at `http://localhost:5000` (adjust port/command to match your actual entry point).

### Building/Updating the FAISS Index

If your knowledge base changes, rebuild the vector index:

```bash
python rag/build_index.py
```

*(Rename to match your actual script.)*

---

## 📱 Responsive Design Notes

ShariahEase is built mobile-first, with:
- A bottom navigation bar on small screens
- A collapsible hamburger sidebar overlay for secondary navigation
- CSS safe-area insets to properly handle notches and rounded corners on modern phones

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome. Feel free to open an issue or submit a pull request.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

---

## 👤 Author

**Engr. Hasnain Zainulabdin**
R&R Digital Solutions

Contact: 03126641281 | [HasnainZainulabdin@gmail.com](mailto:HasnainZainulabdin@gmail.com)
Website: https://hasnainzainulabdin.vercel.app/
