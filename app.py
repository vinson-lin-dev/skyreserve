# app.py
from flask import Flask, render_template, request, redirect, flash, session, url_for
from flask_bcrypt import Bcrypt
from functools import wraps
from datetime import datetime, timedelta
import os
import sqlite3
import re
from urllib.parse import urlparse, unquote
import mysql.connector

from config import (
    DATABASE_URL, SECRET_KEY,
    MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_NAME
)

import random

# --------------------------------------------------------------------------------------
# App init
# --------------------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY
bcrypt = Bcrypt(app)

# --------------------------------------------------------------------------------------
# DB plumbing: SQLite or MySQL depending on DATABASE_URL
# --------------------------------------------------------------------------------------
def _parse_db_url(url: str):
    """
    Returns dict with {driver, path|host|port|user|password|db} from DATABASE_URL.
    Supports:
      - sqlite:///absolute/or/relative/path.db
      - mysql://user:pass@host:port/db
      - mysql+pymysql://... (treated as mysql)
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme.startswith("sqlite"):
        path = parsed.path
        # Remove leading '/' for relative paths on some platforms
        if path.startswith("/") and os.name == "nt":
            path = path.lstrip("/")
        return {"driver": "sqlite", "path": unquote(path)}

    if scheme.startswith("mysql"):
        return {
            "driver": "mysql",
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 3306,
            "db": (parsed.path or "/").lstrip("/")
        }

    # Fallback: treat as unset
    return {"driver": "unknown"}

DB_INFO = _parse_db_url(DATABASE_URL)
IS_SQLITE = DB_INFO.get("driver") == "sqlite"
IS_MYSQL = DB_INFO.get("driver") == "mysql"

def get_db_connection():
    """
    Returns a connection compatible with your existing code.
    - SQLite: sqlite3 with Row factory
    - MySQL: mysql.connector
    - Legacy fallback: mysql.connector with config.py legacy vars
    """
    if IS_SQLITE:
        conn = sqlite3.connect(DB_INFO["path"])
        conn.row_factory = sqlite3.Row
        return conn

    if IS_MYSQL:
        return mysql.connector.connect(
            host=DB_INFO["host"],
            user=DB_INFO["user"],
            password=DB_INFO["password"],
            database=DB_INFO["db"],
            port=DB_INFO["port"],
            autocommit=False,
        )

    # Legacy fallback to config.py MySQL vars if DATABASE_URL is not usable
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_NAME,
        autocommit=False,
    )

def get_cursor(conn, dictionary=False):
    """
    Unifies cursor creation.
    - MySQL: supports dictionary=True
    - SQLite: returns a normal cursor; rows are sqlite3.Row (dict-like) via row_factory.
    """
    if IS_SQLITE:
        return conn.cursor()
    # MySQL
    return conn.cursor(dictionary=dictionary)

def _adapt_query_for_sqlite(q: str) -> str:
    """
    Convert %s placeholders to ? for sqlite and strip backticks.
    (We keep date function differences out of here; those are handled by helper builders below.)
    """
    q = q.replace("`", "")
    # replace %s not inside quotes with ?
    # simple version: swap all %s -> ?, fine because we only use %s
    return q.replace("%s", "?")

# ---------- Date/Time SQL helpers (MySQL vs SQLite) ----------
def sql_now_date():
    return "CURDATE()" if IS_MYSQL else "date('now')"

def sql_date_days_ago(days: int):
    return f"CURDATE() - INTERVAL {days} DAY" if IS_MYSQL else f"date('now','-{days} days')"

def sql_date_months_ago(months: int):
    return f"CURDATE() - INTERVAL {months} MONTH" if IS_MYSQL else f"date('now','-{months} months')"

def sql_date_years_ago(years: int):
    return f"CURDATE() - INTERVAL {years} YEAR" if IS_MYSQL else f"date('now','-{years} years')"

def sql_plus_days(days: int):
    return f"CURDATE() + INTERVAL {days} DAY" if IS_MYSQL else f"date('now','+{days} days')"

def sql_month_group(col: str):
    # returns an expression that yields 'YYYY-MM'
    return f"DATE_FORMAT({col}, '%Y-%m')" if IS_MYSQL else f"strftime('%Y-%m', {col})"

# --------------------------------------------------------------------------------------
# Auth & decorators (unchanged)
# --------------------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            flash('You must be logged in to view this page.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------
@app.route('/')
def home():
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    cursor.execute("SELECT * FROM flight")
    flights = cursor.fetchall()

    cursor.execute("SELECT DISTINCT departure_airport FROM flight")
    departure_airports = cursor.fetchall()

    cursor.execute("SELECT DISTINCT arrival_airport FROM flight")
    arrival_airports = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template('home.html', flights=flights,
                           departure_airports=departure_airports, arrival_airports=arrival_airports)

@app.route('/test')
def test():
    conn = get_db_connection()
    cursor = get_cursor(conn)
    query = "SHOW TABLES;" if IS_MYSQL else "SELECT name FROM sqlite_master WHERE type='table';"
    cursor.execute(query)
    tables = cursor.fetchall()
    cursor.close()
    conn.close()
    if IS_MYSQL:
        return {"tables": [t[0] for t in tables]}
    else:
        return {"tables": [t["name"] if isinstance(t, sqlite3.Row) else t[0] for t in tables]}

@app.route('/flights/<int:flight_num>')
def flight_details(flight_num):
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)
    q = "SELECT * FROM flight WHERE flight_num = %s"
    if IS_SQLITE:
        q = _adapt_query_for_sqlite(q)
    cursor.execute(q, (flight_num,))
    flight = cursor.fetchone()
    cursor.close()
    conn.close()

    if flight is None:
        flash("Flight not found.")
        return redirect(url_for('home'))
    return render_template('flight_details.html', flight=flight)

@app.route('/search', methods=['GET'])
def search_flights():
    source = request.args.get('source')
    destination = request.args.get('destination')
    date = request.args.get('date')

    if not source or not destination or not date:
        flash('All fields are required to search flights.', 'danger')
        return redirect(url_for('home'))

    user_email = session.get('user_email')

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)
    try:
        if user_email:
            # Check booking agent restrictions
            q = "SELECT airline_name FROM booking_agent_work_for WHERE email = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (user_email,))
            booking_agent = cursor.fetchone()

            if booking_agent:
                airline_name = booking_agent['airline_name']
                q = """
                    SELECT * FROM flight
                    WHERE departure_airport = %s 
                      AND arrival_airport = %s 
                      AND DATE(departure_time) = %s
                      AND airline_name = %s
                """
                if IS_SQLITE: q = _adapt_query_for_sqlite(q)
                cursor.execute(q, (source, destination, date, airline_name))
            else:
                q = """
                    SELECT * FROM flight
                    WHERE departure_airport = %s 
                      AND arrival_airport = %s 
                      AND DATE(departure_time) = %s
                """
                if IS_SQLITE: q = _adapt_query_for_sqlite(q)
                cursor.execute(q, (source, destination, date))
        else:
            q = """
                SELECT * FROM flight
                WHERE departure_airport = %s 
                  AND arrival_airport = %s 
                  AND DATE(departure_time) = %s
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (source, destination, date))

        flights = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    return render_template('search_results.html', flights=flights, search_failed=(len(flights) == 0))

# -------------------------------- Auth --------------------------------
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        role = request.form['role']
        email = request.form['email']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')

        conn = get_db_connection()
        cursor = get_cursor(conn)

        # Check existing email
        if role == 'customer':
            q = "SELECT * FROM customer WHERE email = %s"
        elif role == 'booking_agent':
            q = "SELECT * FROM booking_agent WHERE email = %s"
        else:
            q = "SELECT * FROM airline_staff WHERE username = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (email,))
        user = cursor.fetchone()

        if user:
            cursor.close(); conn.close()
            flash('Email is already in use. Please choose another one.', 'danger')
            return redirect(url_for('signup'))

        # Airline name validation (airline_staff only)
        if role == 'airline_staff':
            airline_name = request.form['airline_name']
            q = "SELECT * FROM airline WHERE airline_name = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name,))
            airline = cursor.fetchone()
            if not airline:
                cursor.close(); conn.close()
                flash('Invalid airline name. Please choose a valid airline.', 'danger')
                return redirect(url_for('signup'))

        # Inserts
        try:
            if role == 'customer':
                name = request.form['name']
                building_number = request.form['building_number']
                street = request.form['street']
                city = request.form['city']
                state = request.form['state']
                phone_number = request.form['phone_number']
                passport_number = request.form['passport_number']
                passport_expiration = request.form['passport_expiration']
                passport_country = request.form['passport_country']
                date_of_birth = request.form['date_of_birth']
                q = """
                INSERT INTO customer (email, name, password, building_number, street, city, state, 
                phone_number, passport_number, passport_expiration, passport_country, date_of_birth)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                if IS_SQLITE: q = _adapt_query_for_sqlite(q)
                cursor.execute(q, (email, name, password, building_number, street, city, state, phone_number,
                                   passport_number, passport_expiration, passport_country, date_of_birth))
            elif role == 'booking_agent':
                booking_agent_id = request.form['booking_agent_id']
                q = "INSERT INTO booking_agent (email, password, booking_agent_id) VALUES (%s, %s, %s)"
                if IS_SQLITE: q = _adapt_query_for_sqlite(q)
                cursor.execute(q, (email, password, booking_agent_id))
            else:
                first_name = request.form['first_name']
                last_name = request.form['last_name']
                airline_name = request.form['airline_name']
                date_of_birth = request.form['date_of_birth']
                q = """
                INSERT INTO airline_staff (username, password, first_name, last_name, airline_name, date_of_birth)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
                if IS_SQLITE: q = _adapt_query_for_sqlite(q)
                cursor.execute(q, (email, password, first_name, last_name, airline_name, date_of_birth))

            conn.commit()
        except Exception as e:
            conn.rollback()
            flash(f'Error creating account: {e}', 'danger')
            cursor.close(); conn.close()
            return redirect(url_for('signup'))

        cursor.close(); conn.close()
        flash('Account created successfully! Please log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form['role']
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = get_cursor(conn, dictionary=True)

        if role == 'customer':
            q = "SELECT * FROM customer WHERE email = %s"
        elif role == 'booking_agent':
            q = "SELECT * FROM booking_agent WHERE email = %s"
        else:
            q = "SELECT * FROM airline_staff WHERE username = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (email,))
        user = cursor.fetchone()

        cursor.close()
        conn.close()

        if user and bcrypt.check_password_hash(user['password'], password):
            session['role'] = role
            session['user_email'] = user['email'] if role != 'airline_staff' else user['username']
            if role == 'airline_staff':
                session['airline_name'] = user['airline_name']
            flash(f'Logged in as {role.capitalize()}!', 'success')
            if role == 'customer':
                return redirect(url_for('customer_dashboard'))
            elif role == 'booking_agent':
                return redirect(url_for('booking_agent_dashboard'))
            elif role == 'airline_staff':
                return redirect(url_for('airline_staff_dashboard'))
        else:
            flash('Invalid login credentials. Please try again.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('role', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

# ------------------------------- Customer -------------------------------
@app.route('/customer_dashboard', methods=['GET', 'POST'])
@login_required
def customer_dashboard():
    user_email = session['user_email']
    user_name = session.get('user_name', 'Customer')
    role = session['role']
    if role != 'customer':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try: 
        cursor.execute("SELECT DISTINCT departure_airport FROM flight")
        departure_airports = cursor.fetchall()

        cursor.execute("SELECT DISTINCT arrival_airport FROM flight")
        arrival_airports = cursor.fetchall()

        q = "SELECT * FROM customer WHERE email = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        customer_info = cursor.fetchone()

        # Last 6 months (month-wise)
        month_expr = sql_month_group("p.purchase_date")
        six_months_ago = sql_date_months_ago(6)
        q = f"""
            SELECT {month_expr} AS month, SUM(f.price) AS total_spent
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num
            WHERE p.customer_email = %s AND p.purchase_date >= {six_months_ago}
            GROUP BY month
            ORDER BY month;
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        last_six_months_spending = cursor.fetchall()

        # Total last year
        one_year_ago = sql_date_years_ago(1)
        q = f"""
            SELECT SUM(f.price) AS total_spent
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num
            WHERE p.customer_email = %s AND p.purchase_date >= {one_year_ago};
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        total_spent_last_year_row = cursor.fetchone()
        total_spent_last_year = (total_spent_last_year_row['total_spent'] if total_spent_last_year_row else 0) or 0

        total_spent_last_six_months = sum((row['total_spent'] or 0) for row in last_six_months_spending)

        # Custom range (optional)
        start_date = end_date = None
        monthly_spending = []
        total_spent_range = 0
        if request.method == 'POST':
            start_date = request.form['start_date']
            end_date = request.form['end_date']
            q = f"""
                SELECT {month_expr} AS month, SUM(f.price) AS total_spent
                FROM purchases p
                JOIN ticket t ON p.ticket_id = t.ticket_id
                JOIN flight f ON t.flight_num = f.flight_num
                WHERE p.customer_email = %s AND p.purchase_date BETWEEN %s AND %s
                GROUP BY month
                ORDER BY month;
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (user_email, start_date, end_date))
            monthly_spending = cursor.fetchall()
            total_spent_range = sum((row['total_spent'] or 0) for row in monthly_spending)

        # Upcoming flights
        q = """
            SELECT f.airline_name, f.flight_num, f.departure_time, f.arrival_time, f.departure_airport, 
                   f.arrival_airport, f.status, p.purchase_date, f.price
            FROM flight f
            JOIN ticket t ON f.flight_num = t.flight_num AND f.airline_name = t.airline_name
            JOIN purchases p ON t.ticket_id = p.ticket_id
            WHERE p.customer_email = %s AND f.status = 'upcoming'
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        upcoming_flights = cursor.fetchall()

        # History (not upcoming)
        q = """
            SELECT f.airline_name, f.flight_num, f.departure_time, f.arrival_time, f.departure_airport, 
                   f.arrival_airport, f.status, p.purchase_date, f.price
            FROM flight f
            JOIN ticket t ON f.flight_num = t.flight_num AND f.airline_name = t.airline_name
            JOIN purchases p ON t.ticket_id = p.ticket_id
            WHERE p.customer_email = %s AND f.status != 'upcoming'
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        booking_history = cursor.fetchall()

    finally: 
        cursor.close()
        conn.close()

    return render_template('customer_dashboard.html', 
                           user_name=user_name, user_email=user_email, customer_info=customer_info, 
                           upcoming_flights=upcoming_flights, booking_history=booking_history, 
                           departure_airports=departure_airports, arrival_airports=arrival_airports,
                           total_spent_last_six_months=total_spent_last_six_months,
                           total_spent_last_year=total_spent_last_year,
                           last_six_months_spending=last_six_months_spending,
                           monthly_spending=monthly_spending,
                           total_spent_range=total_spent_range,
                           start_date=start_date, end_date=end_date)

@app.route('/profile')
@login_required
def profile():
    user_email = session['user_email']
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try: 
        q = """
            SELECT name, email, date_of_birth, passport_number, 
                passport_expiration, phone_number, 
                (building_number || ' ' || street || ', ' || city || ', ' || state) AS address,
                passport_country
            FROM customer
            WHERE email = %s
        """
        # For MySQL, CONCAT; for SQLite, || already used above.
        if IS_MYSQL:
            q = """
                SELECT name, email, date_of_birth, passport_number, 
                    passport_expiration, phone_number, 
                    CONCAT(building_number, ' ', street, ', ', city, ', ', state) AS address,
                    passport_country
                FROM customer
                WHERE email = %s
            """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)

        cursor.execute(q, (user_email,))
        user_details = cursor.fetchone()
        if not user_details:
            flash("Profile details not found. Please contact support.", "danger")
            return redirect(url_for('customer_dashboard'))
    finally:
        cursor.close()
        conn.close()
    return render_template('profile.html', user_details=user_details)

@app.route('/purchase_ticket', methods=['POST'])
@login_required
def purchase_ticket():
    flight_num = request.form['flight_num']
    user_email = session['user_email']

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try:
        q = """
            SELECT t.ticket_id 
            FROM ticket t
            LEFT JOIN purchases p ON t.ticket_id = p.ticket_id
            WHERE t.flight_num = %s AND p.ticket_id IS NULL
            LIMIT 1
        """
        if IS_SQLITE:
            # SQLite doesn't support LIMIT with parameterized NULL check differences—this is fine:
            q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (flight_num,))
        available_ticket = cursor.fetchone()

        if not available_ticket:
            flash("No tickets are available for this flight.", "danger")
            return redirect(url_for('customer_dashboard'))

        ticket_id = available_ticket['ticket_id']
        # Insert with today's date
        today_expr = sql_now_date()
        q = f"""
            INSERT INTO purchases (ticket_id, customer_email, purchase_date)
            VALUES (%s, %s, {today_expr})
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (ticket_id, user_email))
        conn.commit()
        flash("Ticket purchased successfully!", "success")
        return redirect(url_for('customer_dashboard'))

    except Exception as e:
        conn.rollback()
        flash(f"An error occurred: {e}", "danger")
        return redirect(url_for('customer_dashboard'))
    finally:
        cursor.close()
        conn.close()

@app.route('/track_spending', methods=['GET', 'POST'])
@login_required
def track_spending():
    user_email = session['user_email']
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try:
        month_expr = sql_month_group("p.purchase_date")
        one_year_ago = sql_date_years_ago(1)
        q = f"""
            SELECT {month_expr} AS month, SUM(f.price) AS total
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num
            WHERE p.customer_email = %s AND p.purchase_date >= {one_year_ago}
            GROUP BY month
            ORDER BY month
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        spending_data = cursor.fetchall()
        months = [row['month'] for row in spending_data]
        spending = [row['total'] for row in spending_data]
    finally:
        cursor.close()
        conn.close()

    return render_template('spending.html', months=months, spending=spending)

# ------------------------------- Booking Agent -------------------------------
@app.route('/booking_agent_dashboard', methods=['GET', 'POST'])
@login_required
def booking_agent_dashboard():
    user_email = session['user_email']
    role = session.get('role')

    if role != 'booking_agent':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    # Get the booking agent ID
    q = "SELECT booking_agent_id FROM booking_agent WHERE email = %s"
    if IS_SQLITE: q = _adapt_query_for_sqlite(q)
    cursor.execute(q, (user_email,))
    booking_agent = cursor.fetchone()
    if not booking_agent:
        cursor.close(); conn.close()
        flash('Booking agent ID not found. Please contact support.', 'danger')
        return redirect(url_for('home'))
    booking_agent_id = booking_agent['booking_agent_id']

    thirty_days_ago_expr = sql_date_days_ago(30)
    six_months_ago_expr = sql_date_months_ago(6)
    one_year_ago_expr = sql_date_years_ago(1)

    try:
        cursor.execute("SELECT DISTINCT departure_airport FROM flight")
        departure_airports = cursor.fetchall()

        cursor.execute("SELECT DISTINCT arrival_airport FROM flight")
        arrival_airports = cursor.fetchall()

        # Airline linked to agent
        q = "SELECT airline_name FROM booking_agent_work_for WHERE email = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        agent_airline = cursor.fetchone()

        if not agent_airline:
            flash('No airline association found for this booking agent.', 'danger')
            return redirect(url_for('home'))

        airline_name = agent_airline['airline_name']

        # Upcoming flights booked (for this airline)
        q = """
            SELECT f.airline_name, f.flight_num, f.departure_time, f.arrival_time, 
                   f.departure_airport, f.arrival_airport, f.price, f.status, p.purchase_date, c.email AS customer_email
            FROM flight f
            JOIN ticket t ON f.flight_num = t.flight_num AND f.airline_name = t.airline_name
            JOIN purchases p ON t.ticket_id = p.ticket_id
            JOIN customer c ON p.customer_email = c.email
            JOIN booking_agent b ON p.booking_agent_id = b.booking_agent_id
            WHERE f.status = 'upcoming' AND f.airline_name = %s AND p.booking_agent_id = b.booking_agent_id
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        upcoming_flights = cursor.fetchall()

        # Commission last 30 days
        q = f"""
            SELECT 
                SUM(f.price * 0.05) AS total_commission, 
                COUNT(p.ticket_id)   AS total_tickets_sold,
                AVG(f.price * 0.05)  AS avg_commission_per_ticket
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = f.airline_name
            WHERE p.booking_agent_id = %s AND p.purchase_date >= {thirty_days_ago_expr}
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (booking_agent_id,))
        data = cursor.fetchone() or {}
        total_commission = data.get('total_commission') or 0
        total_tickets_sold = data.get('total_tickets_sold') or 0
        avg_commission_per_ticket = round((data.get('avg_commission_per_ticket') or 0), 2)

        # Custom range (optional)
        custom_commission = 0
        custom_tickets_sold = 0
        custom_avg_commission = 0
        if request.method == 'POST':
            start_date = request.form['start_date']
            end_date = request.form['end_date']
            q = """
                SELECT SUM(f.price * 0.05) AS total_commission, 
                       COUNT(p.ticket_id)   AS total_tickets_sold,
                       AVG(f.price * 0.05)  AS avg_commission_per_ticket
                FROM purchases p
                JOIN ticket t ON p.ticket_id = t.ticket_id
                JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = f.airline_name
                WHERE p.booking_agent_id = %s AND p.purchase_date BETWEEN %s AND %s
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (booking_agent_id, start_date, end_date))
            d = cursor.fetchone() or {}
            custom_commission = d.get('total_commission') or 0
            custom_tickets_sold = d.get('total_tickets_sold') or 0
            custom_avg_commission = round((d.get('avg_commission_per_ticket') or 0), 2)

        # Top 5 customers (6 months)
        q = f"""
            SELECT p.customer_email, COUNT(p.ticket_id) AS tickets_bought
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            WHERE p.purchase_date >= {six_months_ago_expr}
            GROUP BY p.customer_email
            ORDER BY tickets_bought DESC
            LIMIT 5
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q)
        top_5_customers_by_tickets = cursor.fetchall()

        # Top 5 customers by commission (1 year)
        q = f"""
            SELECT p.customer_email, SUM(f.price * 0.05) AS commission_received
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num
            WHERE p.purchase_date >= {one_year_ago_expr}
            GROUP BY p.customer_email
            ORDER BY commission_received DESC
            LIMIT 5
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q)
        top_5_customers_by_commission = cursor.fetchall()

    except Exception as e:
        flash(f'Error retrieving commission data: {e}', 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()

    return render_template('booking_agent_dashboard.html', upcoming_flights=upcoming_flights,
                           departure_airports=departure_airports, arrival_airports=arrival_airports,
                           total_commission=total_commission,
                           total_tickets_sold=total_tickets_sold,
                           avg_commission_per_ticket=avg_commission_per_ticket,
                           custom_commission=custom_commission,
                           custom_tickets_sold=custom_tickets_sold,
                           custom_avg_commission=custom_avg_commission,
                           top_5_customers_by_tickets=top_5_customers_by_tickets,
                           top_5_customers_by_commission=top_5_customers_by_commission)

# (Agent search/purchase remain same pattern; only placeholder adapts)
@app.route('/agent_search_flights', methods=['GET', 'POST'])
@login_required
def agent_search_flights():
    user_email = session['user_email']
    role = session['role']

    source = destination = date = None

    if request.method == 'POST':
        source = request.form.get('source')
        destination = request.form.get('destination')
        date = request.form.get('date')

    if role != 'booking_agent':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try:
        q = "SELECT airline_name FROM booking_agent_work_for WHERE email = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        booking_agent = cursor.fetchone()

        if not booking_agent:
            flash('No airline association found for this booking agent.', 'danger')
            return redirect(url_for('home'))

        airline_name = booking_agent['airline_name']

        if request.method == 'POST' and (not source or not destination or not date):
            flash('All fields are required to search flights.', 'danger')
            return render_template('agent_search_results.html', 
                                   source=source, destination=destination, date=date,
                                   search_failed=True)

        q = """
            SELECT * FROM flight
            WHERE departure_airport = %s 
              AND arrival_airport = %s 
              AND DATE(departure_time) = %s
              AND airline_name = %s
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (source, destination, date, airline_name))
        flights = cursor.fetchall()

    except Exception as e:
        flash(f"An error occurred: {e}", 'danger')
        flights = []
    finally:
        cursor.close()
        conn.close()

    return render_template('agent_search_results.html', 
                           flights=flights, 
                           search_failed=(len(flights) == 0),
                           source=source, destination=destination, date=date)

@app.route('/agent_purchase_ticket', methods=['POST'])
@login_required
def agent_purchase_ticket():
    user_email = session['user_email']
    role = session['role']

    if role != 'booking_agent':
        flash('You do not have permission to perform this action.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try:
        q = "SELECT booking_agent_id FROM booking_agent WHERE email = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        booking_agent = cursor.fetchone()
        if not booking_agent:
            flash('Booking agent ID not found. Please contact support.', 'danger')
            return redirect(url_for('agent_search_flights'))

        booking_agent_id = booking_agent['booking_agent_id']

        q = "SELECT airline_name FROM booking_agent_work_for WHERE email = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        agent_airline = cursor.fetchone()
        if not agent_airline:
            flash('No airline association found for this booking agent.', 'danger')
            return redirect(url_for('agent_search_flights'))

        airline_name = agent_airline['airline_name']
        flight_num = request.form['flight_num']
        customer_email = request.form['customer_email']

        q = """
            SELECT t.ticket_id 
            FROM ticket t
            LEFT JOIN purchases p ON t.ticket_id = p.ticket_id
            WHERE t.flight_num = %s AND p.ticket_id IS NULL
            LIMIT 1
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (flight_num,))
        ticket = cursor.fetchone()
        if not ticket:
            flash('No available tickets for this flight.', 'danger')
            return redirect(url_for('agent_search_flights'))

        ticket_id = ticket['ticket_id']

        q = "SELECT * FROM customer WHERE email = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (customer_email,))
        customer = cursor.fetchone()
        if not customer:
            flash('Customer email not found. Please check the email and try again.', 'danger')
            return redirect(url_for('agent_search_flights'))

        today_expr = sql_now_date()
        q = f"""
            INSERT INTO purchases (ticket_id, customer_email, booking_agent_id, purchase_date)
            VALUES (%s, %s, %s, {today_expr})
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (ticket_id, customer_email, booking_agent_id))
        conn.commit()

        flash('Flight successfully booked for customer!', 'success')
        return redirect(url_for('booking_agent_dashboard'))

    except Exception as e:
        conn.rollback()
        flash(f'Error booking flight: {e}', 'danger')
        return redirect(url_for('booking_agent_dashboard'))
    finally:
        cursor.close()
        conn.close()

# ------------------------------- Airline Staff -------------------------------
def check_admin_permissions(user_email):
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)
    q = "SELECT 1 FROM permission WHERE username = %s AND permission_type = 'Admin'"
    if IS_SQLITE: q = _adapt_query_for_sqlite(q)
    cursor.execute(q, (user_email,))
    result = cursor.fetchone()
    cursor.close(); conn.close()
    return result is not None

def check_operator_permission(user_email):
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)
    q = "SELECT 1 FROM permission WHERE username = %s AND permission_type = 'Operator'"
    if IS_SQLITE: q = _adapt_query_for_sqlite(q)
    cursor.execute(q, (user_email,))
    result = cursor.fetchone()
    cursor.close(); conn.close()
    return result is not None

@app.route('/airline_staff_dashboard', methods=['GET', 'POST'])
@login_required
def airline_staff_dashboard():
    user_email = session['user_email']
    role = session['role']

    if role != 'airline_staff':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    if check_admin_permissions(user_email) and check_operator_permission(user_email):
        flash('You are logged in as an admin and operator.', 'success')
    elif check_admin_permissions(user_email):
        flash('You are logged in as an admin.', 'success')
    elif check_operator_permission(user_email):
        flash('You are logged in as an operator.', 'success')
    else:
        flash('You are logged in as a regular airline staff member.', 'success')

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try:
        q = "SELECT airline_name FROM airline_staff WHERE username = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        airline_name = cursor.fetchone()['airline_name']

        now_expr = sql_now_date()
        plus_30 = sql_plus_days(30)
        q = f"""
            SELECT f.flight_num, f.departure_time, f.arrival_time, f.departure_airport, f.arrival_airport,
                   f.airline_name, COUNT(p.ticket_id) AS num_customers
            FROM flight f
            LEFT JOIN ticket t ON f.flight_num = t.flight_num AND f.airline_name = t.airline_name
            LEFT JOIN purchases p ON t.ticket_id = p.ticket_id
            WHERE f.airline_name = %s AND f.departure_time BETWEEN {now_expr} AND {plus_30}
            GROUP BY f.flight_num
            ORDER BY f.departure_time
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        flights = cursor.fetchall()

        if request.method == 'POST':
            start_date = request.form['start_date']
            end_date = request.form['end_date']
            source_airport = request.form['source_airport']
            destination_airport = request.form['destination_airport']
            q = """
                SELECT f.flight_num, f.departure_time, f.arrival_time, f.departure_airport, f.arrival_airport,
                       COUNT(p.ticket_id) AS num_customers
                FROM flight f
                LEFT JOIN ticket t ON f.flight_num = t.flight_num AND f.airline_name = t.airline_name
                LEFT JOIN purchases p ON t.ticket_id = p.ticket_id
                WHERE f.airline_name = %s
                  AND f.departure_time BETWEEN %s AND %s
                  AND f.departure_airport LIKE %s
                  AND f.arrival_airport LIKE %s
                GROUP BY f.flight_num
                ORDER BY f.departure_time
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, start_date, end_date, f'%{source_airport}%', f'%{destination_airport}%'))
            flights = cursor.fetchall()

        # Customers per flight
        flight_customers = {}
        for flight in flights:
            q = """
                SELECT c.name, c.email
                FROM purchases p
                JOIN ticket t ON p.ticket_id = t.ticket_id
                JOIN customer c ON p.customer_email = c.email
                WHERE t.flight_num = %s AND t.airline_name = %s
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (flight['flight_num'], airline_name))
            customers = cursor.fetchall()
            flight_customers[flight['flight_num']] = customers

    except Exception as e:
        flash(f'Error retrieving flight data: {e}', 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()

    return render_template('airline_staff_dashboard.html',
                           flights=flights, flight_customers=flight_customers,
                           check_admin_permissions=check_admin_permissions,
                           check_operator_permission=check_operator_permission)

@app.route('/grant_permissions', methods=['GET', 'POST'])
@login_required
def grant_permissions():
    user_email = session['user_email']
    role = session['role']

    if role != 'airline_staff' or not check_admin_permissions(user_email):
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    if request.method == 'POST':
        staff_username = request.form['staff_username']
        new_permission = request.form['new_permission']

        try:
            q = "SELECT * FROM airline_staff WHERE username = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (staff_username,))
            staff = cursor.fetchone()
            if not staff:
                flash('Staff member not found.', 'danger')
                return redirect(url_for('grant_permissions'))

            q = "SELECT * FROM permission WHERE username = %s AND permission_type = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (staff_username, new_permission))
            existing_permission = cursor.fetchone()
            if existing_permission:
                flash('This staff member already has this permission.', 'danger')
                return redirect(url_for('grant_permissions'))

            q = "INSERT INTO permission (username, permission_type) VALUES (%s, %s)"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (staff_username, new_permission))
            conn.commit()
            flash('Permission granted successfully!', 'success')
            return redirect(url_for('grant_permissions'))

        except Exception as e:
            conn.rollback()
            flash(f'Error granting permission: {e}', 'danger')
            return redirect(url_for('grant_permissions'))
        finally:
            cursor.close()
            conn.close()

    # staff list (same connection pattern)
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)
    q = "SELECT username, first_name, last_name FROM airline_staff WHERE airline_name = %s"
    if IS_SQLITE: q = _adapt_query_for_sqlite(q)
    cursor.execute(q, (session['airline_name'],))
    staff_members = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template('grant_permissions.html', staff_members=staff_members)

def generate_booking_agent_id():
    return random.randint(1000, 9999)

@app.route('/create_flight', methods=['GET', 'POST'])
@login_required
def create_flight():
    user_email = session['user_email']
    role = session['role']

    if role != 'airline_staff' or not check_admin_permissions(user_email):
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    if request.method == 'POST':
        flight_num = request.form['flight_num']
        departure_airport = request.form['departure_airport']
        arrival_airport = request.form['arrival_airport']
        departure_time = request.form['departure_time']
        arrival_time = request.form['arrival_time']
        price = request.form['price']
        status = request.form['status']
        airplane_id = request.form['airplane_id']
        airline_name = session['airline_name']

        try:
            q = "SELECT * FROM flight WHERE airline_name = %s AND flight_num = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, flight_num))
            existing_flight = cursor.fetchone()
            if existing_flight:
                flash('Flight number already exists for this airline.', 'danger')
                return redirect(url_for('create_flight'))

            q = """
                INSERT INTO flight (airline_name, flight_num, departure_airport, departure_time,
                                    arrival_airport, arrival_time, price, status, airplane_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, flight_num, departure_airport, departure_time,
                               arrival_airport, arrival_time, price, status, airplane_id))
            conn.commit()

            q = "SELECT seats FROM airplane WHERE airline_name = %s AND airplane_id = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, airplane_id))
            airplane = cursor.fetchone()
            if airplane:
                number_of_seats = airplane['seats']
                for _ in range(number_of_seats):
                    q = "INSERT INTO ticket (airline_name, flight_num) VALUES (%s, %s)"
                    if IS_SQLITE: q = _adapt_query_for_sqlite(q)
                    cursor.execute(q, (airline_name, flight_num))
                conn.commit()

            flash('Flight created successfully and tickets added!', 'success')
            return redirect(url_for('airline_staff_dashboard'))

        except Exception as e:
            conn.rollback()
            flash(f'Error creating flight: {e}', 'danger')
            return redirect(url_for('create_flight'))
        finally:
            cursor.close()
            conn.close()

    # GET: load airports & airplanes
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)
    cursor.execute("SELECT airport_name FROM airport")
    airports = cursor.fetchall()
    q = "SELECT airplane_id FROM airplane WHERE airline_name = %s"
    if IS_SQLITE: q = _adapt_query_for_sqlite(q)
    cursor.execute(q, (session['airline_name'],))
    airplanes = cursor.fetchall()
    cursor.close(); conn.close()

    return render_template('create_flight.html', airports=airports, airplanes=airplanes)

@app.route('/change_flight_status/<airline_name>/<int:flight_num>', methods=['GET', 'POST'])
@login_required
def change_flight_status(airline_name, flight_num):
    user_email = session['user_email']
    role = session['role']

    if role != 'airline_staff' or not check_operator_permission(user_email):
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    if request.method == 'POST':
        new_status = request.form['status']
        try:
            q = "UPDATE flight SET status = %s WHERE airline_name = %s AND flight_num = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (new_status, airline_name, flight_num))
            conn.commit()
            flash('Flight status updated successfully!', 'success')
            return redirect(url_for('airline_staff_dashboard'))
        except Exception as e:
            conn.rollback()
            flash(f'Error updating flight status: {e}', 'danger')
            return redirect(url_for('airline_staff_dashboard'))
        finally:
            cursor.close()
            conn.close()

    q = """
        SELECT flight_num, departure_airport, arrival_airport, departure_time, arrival_time, status
        FROM flight 
        WHERE airline_name = %s AND flight_num = %s
    """
    if IS_SQLITE: q = _adapt_query_for_sqlite(q)
    cursor.execute(q, (airline_name, flight_num))
    flight = cursor.fetchone()
    cursor.close(); conn.close()

    if not flight:
        flash('Flight not found.', 'danger')
        return redirect(url_for('airline_staff_dashboard'))

    return render_template('change_flight_status.html', flight=flight)

@app.route('/add_airplane', methods=['GET', 'POST'])
@login_required
def add_airplane():
    user_email = session['user_email']
    role = session['role']

    if role != 'airline_staff' or not check_admin_permissions(user_email):
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    if request.method == 'POST':
        airplane_id = request.form['airplane_id']
        seats = request.form['seats']
        airline_name = session['airline_name']

        try:
            q = "SELECT * FROM airplane WHERE airline_name = %s AND airplane_id = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, airplane_id))
            existing_airplane = cursor.fetchone()
            if existing_airplane:
                flash('Airplane ID already exists for this airline.', 'danger')
                return redirect(url_for('add_airplane'))

            q = "INSERT INTO airplane (airline_name, airplane_id, seats) VALUES (%s, %s, %s)"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, airplane_id, seats))
            conn.commit()

            flash('Airplane added successfully!', 'success')
            return redirect(url_for('add_airplane'))

        except Exception as e:
            conn.rollback()
            flash(f'Error adding airplane: {e}', 'danger')
            return redirect(url_for('add_airplane'))
        finally:
            cursor.close()
            conn.close()

    return render_template('add_airplane.html')

@app.route('/airplane_list')
@login_required
def airplane_list():
    user_email = session['user_email']
    role = session['role']

    if role != 'airline_staff' or not check_admin_permissions(user_email):
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try:
        airline_name = session['airline_name']
        q = "SELECT airplane_id, seats FROM airplane WHERE airline_name = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        airplanes = cursor.fetchall()
    except Exception as e:
        flash(f'Error retrieving airplanes: {e}', 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()

    return render_template('airplane_list.html', airplanes=airplanes)

@app.route('/add_airport', methods=['GET', 'POST'])
@login_required
def add_airport():
    user_email = session['user_email']
    role = session['role']

    if role != 'airline_staff' or not check_admin_permissions(user_email):
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    if request.method == 'POST':
        airport_name = request.form['airport_name']
        airport_city = request.form['airport_city']

        try:
            q = "SELECT * FROM airport WHERE airport_name = %s"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airport_name,))
            existing_airport = cursor.fetchone()
            if existing_airport:
                flash('Airport already exists in the system.', 'danger')
                return redirect(url_for('add_airport'))

            q = "INSERT INTO airport (airport_name, airport_city) VALUES (%s, %s)"
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airport_name, airport_city))
            conn.commit()

            flash('Airport added successfully!', 'success')
            return redirect(url_for('add_airport'))

        except Exception as e:
            conn.rollback()
            flash(f'Error adding airport: {e}', 'danger')
            return redirect(url_for('add_airport'))
        finally:
            cursor.close()
            conn.close()

    return render_template('add_airport.html')

@app.route('/airport_list')
@login_required
def airport_list():
    user_email = session['user_email']
    role = session['role']

    if role != 'airline_staff' or not check_admin_permissions(user_email):
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try:
        cursor.execute("SELECT airport_name, airport_city FROM airport")
        airports = cursor.fetchall()
    except Exception as e:
        flash(f'Error retrieving airports: {e}', 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()
    return render_template('airport_list.html', airports=airports)

@app.route('/view_booking_agents')
@login_required
def view_booking_agents():
    user_email = session['user_email']
    role = session['role']
    if role != 'airline_staff':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    try:
        q = "SELECT airline_name FROM airline_staff WHERE username = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        airline_name = cursor.fetchone()['airline_name']

        one_month_ago = sql_date_months_ago(1)
        one_year_ago = sql_date_years_ago(1)

        q = f"""
            SELECT ba.email, COUNT(p.ticket_id) AS tickets_sold
            FROM booking_agent ba
            LEFT JOIN purchases p ON ba.booking_agent_id = p.booking_agent_id
            WHERE p.purchase_date >= {one_month_ago} OR p.purchase_date IS NULL
            GROUP BY ba.email
            ORDER BY tickets_sold DESC
            LIMIT 5
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q)
        top_agents_by_sales_month = cursor.fetchall()

        q = f"""
            SELECT ba.email, COUNT(p.ticket_id) AS tickets_sold
            FROM booking_agent ba
            LEFT JOIN purchases p ON ba.booking_agent_id = p.booking_agent_id
            WHERE p.purchase_date >= {one_year_ago} OR p.purchase_date IS NULL
            GROUP BY ba.email
            ORDER BY tickets_sold DESC
            LIMIT 5
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q)
        top_agents_by_sales_year = cursor.fetchall()

        q = f"""
            SELECT ba.email, COALESCE(SUM(f.price * 0.05), 0) AS commission_received
            FROM booking_agent ba
            LEFT JOIN purchases p ON ba.booking_agent_id = p.booking_agent_id
            LEFT JOIN ticket t ON p.ticket_id = t.ticket_id
            LEFT JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = f.airline_name
            WHERE p.purchase_date >= {one_year_ago} OR p.purchase_date IS NULL
            GROUP BY ba.email
            ORDER BY commission_received DESC
            LIMIT 5
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q)
        top_agents_by_commission = cursor.fetchall()

    except Exception as e:
        flash(f'Error retrieving booking agents: {e}', 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()

    return render_template('view_booking_agents.html', 
                           top_agents_by_sales_month=top_agents_by_sales_month,
                           top_agents_by_sales_year=top_agents_by_sales_year,
                           top_agents_by_commission=top_agents_by_commission)

@app.route('/view_frequent_customers', methods=['GET', 'POST'])
@login_required
def view_frequent_customers():
    user_email = session['user_email']
    role = session['role']
    if role != 'airline_staff':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)
    
    try:
        q = "SELECT airline_name FROM airline_staff WHERE username = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        airline_name = cursor.fetchone()['airline_name']
        
        one_year_ago = sql_date_years_ago(1)
        q = f"""
            SELECT c.email, c.name, COUNT(p.ticket_id) AS num_tickets
            FROM customer c
            JOIN purchases p ON c.email = p.customer_email
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num AND f.airline_name = %s
            WHERE p.purchase_date >= {one_year_ago}
            GROUP BY c.email
            ORDER BY num_tickets DESC
            LIMIT 5
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        frequent_customers = cursor.fetchall()

        customer_flights = []
        selected_customer = None
        if request.method == 'POST' and 'customer_email' in request.form:
            selected_customer_email = request.form['customer_email']
            q = """
                SELECT f.flight_num, f.departure_time, f.arrival_time, f.departure_airport, f.arrival_airport
                FROM purchases p
                JOIN ticket t ON p.ticket_id = t.ticket_id
                JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
                WHERE p.customer_email = %s
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, selected_customer_email))
            customer_flights = cursor.fetchall()
            selected_customer = next((c for c in frequent_customers if c['email'] == selected_customer_email), None)
        
    except Exception as e:
        flash(f"Error retrieving customer data: {e}", 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()

    return render_template('view_frequent_customers.html', 
                           frequent_customers=frequent_customers, 
                           customer_flights=customer_flights,
                           selected_customer=selected_customer)

@app.route('/view_reports', methods=['GET', 'POST'])
@login_required
def view_reports():
    user_email = session['user_email']
    role = session['role']
    if role != 'airline_staff':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    total_sales = 0
    month_wise_sales = []
    start_date = None
    end_date = None

    try:
        q = "SELECT airline_name FROM airline_staff WHERE username = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        airline_name = cursor.fetchone()['airline_name']

        month_expr = sql_month_group("p.purchase_date")

        if request.method == 'POST':
            start_date = request.form['start_date']
            end_date = request.form['end_date']

            q = """
                SELECT COUNT(p.ticket_id) AS total_sales
                FROM purchases p
                JOIN ticket t ON p.ticket_id = t.ticket_id
                JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
                WHERE p.purchase_date BETWEEN %s AND %s
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, start_date, end_date))
            total_sales = cursor.fetchone()['total_sales'] or 0

            q = f"""
                SELECT {month_expr} AS month, COUNT(p.ticket_id) AS tickets_sold
                FROM purchases p
                JOIN ticket t ON p.ticket_id = t.ticket_id
                JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
                WHERE p.purchase_date BETWEEN %s AND %s
                GROUP BY month
                ORDER BY month
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name, start_date, end_date))
            month_wise_sales = cursor.fetchall()

        else:
            one_year_ago = sql_date_years_ago(1)
            q = f"""
                SELECT COUNT(p.ticket_id) AS total_sales
                FROM purchases p
                JOIN ticket t ON p.ticket_id = t.ticket_id
                JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
                WHERE p.purchase_date >= {one_year_ago}
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name,))
            total_sales = cursor.fetchone()['total_sales'] or 0

            q = f"""
                SELECT {month_expr} AS month, COUNT(p.ticket_id) AS tickets_sold
                FROM purchases p
                JOIN ticket t ON p.ticket_id = t.ticket_id
                JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
                WHERE p.purchase_date >= {one_year_ago}
                GROUP BY month
                ORDER BY month
            """
            if IS_SQLITE: q = _adapt_query_for_sqlite(q)
            cursor.execute(q, (airline_name,))
            month_wise_sales = cursor.fetchall()

    except Exception as e:
        flash(f'Error retrieving report data: {e}', 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()

    return render_template('view_reports.html', total_sales=total_sales,
                           month_wise_sales=month_wise_sales, start_date=start_date, end_date=end_date)

@app.route('/view_revenue_comparison', methods=['GET'])
@login_required
def view_revenue_comparison():
    user_email = session['user_email']
    role = session['role']
    if role != 'airline_staff':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    direct_revenue_last_month = 0
    indirect_revenue_last_month = 0
    direct_revenue_last_year = 0
    indirect_revenue_last_year = 0

    try:
        q = "SELECT airline_name FROM airline_staff WHERE username = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        airline_name = cursor.fetchone()['airline_name']

        one_month_ago = sql_date_months_ago(1)
        one_year_ago = sql_date_years_ago(1)

        q = f"""
            SELECT SUM(f.price) AS direct_revenue
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
            WHERE p.booking_agent_id IS NULL AND p.purchase_date >= {one_month_ago}
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        direct_revenue_last_month = cursor.fetchone()['direct_revenue'] or 0

        q = f"""
            SELECT SUM(f.price) AS indirect_revenue
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
            WHERE p.booking_agent_id IS NOT NULL AND p.purchase_date >= {one_month_ago}
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        indirect_revenue_last_month = cursor.fetchone()['indirect_revenue'] or 0

        q = f"""
            SELECT SUM(f.price) AS direct_revenue
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
            WHERE p.booking_agent_id IS NULL AND p.purchase_date >= {one_year_ago}
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        direct_revenue_last_year = cursor.fetchone()['direct_revenue'] or 0

        q = f"""
            SELECT SUM(f.price) AS indirect_revenue
            FROM purchases p
            JOIN ticket t ON p.ticket_id = t.ticket_id
            JOIN flight f ON t.flight_num = f.flight_num AND t.airline_name = %s
            WHERE p.booking_agent_id IS NOT NULL AND p.purchase_date >= {one_year_ago}
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        indirect_revenue_last_year = cursor.fetchone()['indirect_revenue'] or 0

    except Exception as e:
        flash(f'Error retrieving revenue data: {e}', 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()

    return render_template('view_revenue_comparison.html', 
                           direct_revenue_last_month=direct_revenue_last_month,
                           indirect_revenue_last_month=indirect_revenue_last_month,
                           direct_revenue_last_year=direct_revenue_last_year,
                           indirect_revenue_last_year=indirect_revenue_last_year)

@app.route('/view_top_destinations', methods=['GET'])
@login_required
def view_top_destinations():
    user_email = session['user_email']
    role = session['role']
    if role != 'airline_staff':
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('home'))

    conn = get_db_connection()
    cursor = get_cursor(conn, dictionary=True)

    top_destinations_last_3_months = []
    top_destinations_last_year = []

    try:
        q = "SELECT airline_name FROM airline_staff WHERE username = %s"
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (user_email,))
        airline_name = cursor.fetchone()['airline_name']

        three_months_ago = sql_date_months_ago(3)
        one_year_ago = sql_date_years_ago(1)

        q = f"""
            SELECT f.arrival_airport, a.airport_city, COUNT(f.flight_num) AS num_flights
            FROM flight f
            JOIN airport a ON f.arrival_airport = a.airport_name
            WHERE f.airline_name = %s AND f.departure_time >= {three_months_ago}
            GROUP BY f.arrival_airport
            ORDER BY num_flights DESC
            LIMIT 3
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        top_destinations_last_3_months = cursor.fetchall()

        q = f"""
            SELECT f.arrival_airport, a.airport_city, COUNT(f.flight_num) AS num_flights
            FROM flight f
            JOIN airport a ON f.arrival_airport = a.airport_name
            WHERE f.airline_name = %s AND f.departure_time >= {one_year_ago}
            GROUP BY f.arrival_airport
            ORDER BY num_flights DESC
            LIMIT 3
        """
        if IS_SQLITE: q = _adapt_query_for_sqlite(q)
        cursor.execute(q, (airline_name,))
        top_destinations_last_year = cursor.fetchall()

    except Exception as e:
        flash(f'Error retrieving top destinations data: {e}', 'danger')
        return redirect(url_for('home'))
    finally:
        cursor.close()
        conn.close()

    return render_template('view_top_destinations.html',
                           top_destinations_last_3_months=top_destinations_last_3_months,
                           top_destinations_last_year=top_destinations_last_year)

# --------------------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, port=5002)