# 🏏 IPL NLP to SQL

An AI-powered Streamlit application that converts natural language questions into SQL queries for IPL cricket datasets using Google Gemini and Snowflake.

---

## 🚀 Features

* 🤖 Natural Language → SQL using Gemini AI
* 🗄️ Snowflake database integration
* 📊 Interactive Streamlit dashboard
* ⚡ Persistent cached Snowflake connections
* 📈 Admin analytics dashboard
* 📜 Query logging system
* 📥 CSV export support
* 🔐 Admin authentication
* 🧠 YAML-based schema understanding
* 🚀 Optimized performance with Streamlit caching

---

## 🛠️ Tech Stack

### Frontend

* Streamlit

### Backend

* Python
* Snowflake
* Google Gemini API

### Libraries Used

* pandas
* pyyaml
* python-dotenv
* snowflake-connector-python
* google-genai

---

## 📂 Project Structure

```bash
IPL-NLP-TO-SQL/
│
├── app.py
├── .env
├── requirements.txt
├── schema.yaml
├── .gitignore
└── README.md
```

---

## ⚙️ Installation

### 1️⃣ Clone the Repository

```bash
git clone https://github.com/GURUMITHRAN-15/NLP-TO-SQL.git
cd NLP-TO-SQL
```

---

### 2️⃣ Create Virtual Environment

```bash
python -m venv venv
```

### 3️⃣ Activate Virtual Environment

#### Windows (PowerShell)

```bash
.\venv\Scripts\Activate
```

#### Linux / Mac

```bash
source venv/bin/activate
```

---

### 4️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 🔑 Environment Variables

Create a `.env` file in the root folder.

```env
GEMINI_API_KEY=your_api_key

SNOWFLAKE_USER=your_user
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_ACCOUNT=your_account
SNOWFLAKE_WAREHOUSE=your_warehouse
SNOWFLAKE_DATABASE=your_database
SNOWFLAKE_SCHEMA=your_schema

SCHEMA_FILE_PATH=schema.yaml

ADMIN_PASSWORD=your_admin_password
```

---

## ▶️ Run the Application

```bash
streamlit run app.py
```

---

## 💡 Example Queries

* Which batsman scored the most runs?
* Top 10 wicket takers in IPL history
* Matches played in Mumbai
* Highest strike rate among openers
* Most sixes in a season

---

## 🛡️ Admin Dashboard

The application includes an admin panel with:

* Live query monitoring
* User session tracking
* Query analytics
* SQL inspection
* Database statistics
* CSV export

---

## 📊 Performance Optimizations

* Cached Gemini client
* Persistent Snowflake connection
* Cached schema loading
* Background async logging
* Reduced rerun overhead

---

## 🔒 Security

* Environment variable based credential management
* Admin password hashing support
* Read-only SQL generation
* Query logging

---

## 📸 Screenshots

Add your screenshots here.

---

## 🤝 Contributing

Contributions are welcome.

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to your branch
5. Create a Pull Request

---

## 📄 License

This project is licensed under the MIT License.

---

## 👨‍💻 Author

Developed by GURUMITHRAN

GitHub: https://github.com/GURUMITHRAN-15
