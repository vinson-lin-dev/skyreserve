# Initializes a SQLite DB for SkyReserve with fresh demo data.
# Usage: python init_sqlite.py
import os, sqlite3, pathlib, random
from datetime import datetime, timedelta
import bcrypt

BASE = pathlib.Path(__file__).resolve().parent
DB = BASE / "instance" / "skyreserve.db"
SCHEMA = BASE / "resources" / "schema.sql"

DAYS_AHEAD = int(os.getenv("SEED_DAYS_AHEAD", "30"))
ROUTES_PER_AIRLINE_PER_DAY = int(os.getenv("SEED_ROUTES_PER_DAY", "3"))
TICKETS_PER_FLIGHT = int(os.getenv("SEED_TICKETS_PER_FLIGHT", "25"))
PURCHASE_FILL_RATE = float(os.getenv("SEED_PURCHASE_FILL_RATE", "0.4"))
RANDOM_SEED = int(os.getenv("SEED_RANDOM", "42"))

random.seed(RANDOM_SEED)
os.makedirs(DB.parent, exist_ok=True)

def execmany(conn, q, rows):
    conn.executemany(q, rows)

def up(conn, q, args=None):
    if args is None:
        args = []
    conn.execute(q, args)

def iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def hashpw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def main():
    conn = sqlite3.connect(DB.as_posix())
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    airlines = [("American Airlines",), ("China Eastern",), ("Delta Airlines",), ("United Airlines",)]
    execmany(conn, "INSERT OR IGNORE INTO airline(airline_name) VALUES (?)", airlines)

    airports = [
        ("ATL","Atlanta"), ("BOS","Boston"), ("DEN","Denver"), ("DFW","Dallas"),
        ("DXB","Dubai"), ("JFK","New York"), ("LAX","Los Angeles"), ("ORD","Chicago"),
        ("PVG","Shanghai"), ("SEA","Seattle"), ("SFO","San Francisco"), ("MIA","Miami")
    ]
    execmany(conn, "INSERT OR IGNORE INTO airport(airport_name, airport_city) VALUES (?,?)", airports)

    airplanes = [
        ("American Airlines", 303, 300),
        ("China Eastern", 1, 200), ("China Eastern", 2, 300),
        ("Delta Airlines", 202, 250),
        ("United Airlines", 101, 200)
    ]
    execmany(conn, "INSERT OR IGNORE INTO airplane(airline_name, airplane_id, seats) VALUES (?,?,?)", airplanes)

    pw = hashpw("demo1234")
    staff = [
        ("airlinestaff@demo.com", pw, "Alice", "Admin", "1990-01-01", "American Airlines"),
        ("operator@demo.com",    pw, "Oscar", "Operator", "1992-02-02", "China Eastern"),
        ("staff@staff.com",      pw, "Eve",   "Staff",    "1995-05-05", "China Eastern")
    ]
    execmany(conn, "INSERT OR IGNORE INTO airline_staff(username, password, first_name, last_name, date_of_birth, airline_name) VALUES (?,?,?,?,?,?)", staff)

    perms = [("airlinestaff@demo.com","Admin"), ("airlinestaff@demo.com","Operator"), ("operator@demo.com","Operator")]
    execmany(conn, "INSERT OR IGNORE INTO permission(username, permission_type) VALUES (?,?)", perms)

    agents = [("booking@demo.com", pw, 1), ("b@b.com", pw, 3)]
    execmany(conn, "INSERT OR IGNORE INTO booking_agent(email, password, booking_agent_id) VALUES (?,?,?)", agents)

    agent_links = [("booking@demo.com","China Eastern"), ("b@b.com","China Eastern"), ("b@b.com","American Airlines")]
    execmany(conn, "INSERT OR IGNORE INTO booking_agent_work_for(email, airline_name) VALUES (?,?)", agent_links)

    customers = [
        ("customer@demo.com", "Customer Demo", pw, "123", "Main St", "New York", "NY", "2120000000", "P123456", "2030-01-01", "USA", "1998-03-03"),
        ("janesmith@gmail.com", "Jane Smith", pw, "321", "Broadway", "New York", "NY", "2121111111", "A987654321", "2031-06-10", "USA", "1997-07-07"),
        ("johndoe@gmail.com",   "John Doe",   pw, "456", "Market",   "San Francisco", "CA", "4159999999", "A123456789", "2032-09-09", "USA", "1995-09-09"),
        ("c@c.com",             "c",          pw, "1",   "Street",   "City", "ST", "1234", "1234", "2033-01-01", "USA", "2000-01-01"),
        ("c2@c.com",            "c2",         pw, "2",   "Street",   "City", "ST", "5678", "5678", "2033-01-02", "USA", "2000-01-02")
    ]
    execmany(conn, "INSERT OR IGNORE INTO customer(email, name, password, building_number, street, city, state, phone_number, passport_number, passport_expiration, passport_country, date_of_birth) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", customers)

    now = datetime.utcnow()
    routes = [
        ("JFK","LAX", 6.0, 60), ("LAX","JFK", 6.0, 60),
        ("JFK","ATL", 2.5, 40), ("ATL","JFK", 2.5, 40),
        ("ORD","DEN", 2.5, 40), ("DEN","ORD", 2.5, 40),
        ("JFK","PVG", 14.0, 120), ("PVG","JFK", 13.5, 120),
        ("LAX","ORD", 4.0, 55), ("ORD","LAX", 4.0, 55)
    ]
    airlines_cfg = [("American Airlines",303,100), ("China Eastern",1,800), ("Delta Airlines",202,200), ("United Airlines",101,300)]

    def price_for(duration_h, base):
        p = base * duration_h
        p *= random.uniform(0.8, 1.2)
        return round(max(60, p), 2)

    counters = {a: start for (a, _, start) in airlines_cfg}
    flights_to_insert = []
    from random import sample
    for day in range(DAYS_AHEAD):
        day_base = now + timedelta(days=day)
        for (airline, airplane_id, _) in airlines_cfg:
            day_routes = sample(routes, k=min(ROUTES_PER_AIRLINE_PER_DAY, len(routes)))
            for idx, (src, dst, hours, basep) in enumerate(day_routes):
                dep_time = day_base.replace(hour=8 + 3*idx, minute=0, second=0, microsecond=0)
                arr_time = dep_time + timedelta(hours=hours)
                if arr_time < now:
                    status = "completed"
                elif dep_time <= now <= arr_time:
                    status = "in-progress"
                else:
                    status = "upcoming"
                    if random.random() < 0.05:
                        status = "delayed"
                price = price_for(hours, basep)
                flight_num = counters[airline]; counters[airline] += 1
                flights_to_insert.append((airline, flight_num, src, iso(dep_time), dst, iso(arr_time), price, status, airplane_id))

    execmany(conn, "INSERT OR IGNORE INTO flight (airline_name, flight_num, departure_airport, departure_time, arrival_airport, arrival_time, price, status, airplane_id) VALUES (?,?,?,?,?,?,?,?,?)", flights_to_insert)

    cur = conn.cursor()
    cur.execute("SELECT airline_name, flight_num, departure_time FROM flight")
    all_flights = cur.fetchall()

    cur.execute("SELECT booking_agent_id FROM booking_agent")
    agent_ids = [row[0] for row in cur.fetchall()]
    cur.execute("SELECT email FROM customer")
    customer_emails = [row[0] for row in cur.fetchall()]

    tickets_inserted = 0
    purchases_inserted = 0

    for airline_name, flight_num, departure_time in all_flights:
        for _ in range(TICKETS_PER_FLIGHT):
            up(conn, "INSERT INTO ticket(airline_name, flight_num) VALUES (?, ?)", (airline_name, flight_num))
        last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        first_id = last_id - TICKETS_PER_FLIGHT + 1
        ticket_ids = list(range(first_id, last_id + 1))
        tickets_inserted += len(ticket_ids)

        k = int(PURCHASE_FILL_RATE * len(ticket_ids))
        purchased = random.sample(ticket_ids, k=k) if k > 0 else []
        dep_dt = datetime.strptime(departure_time, "%Y-%m-%d %H:%M:%S")

        now_dt = datetime.utcnow()
        for tid in purchased:
            cust = random.choice(customer_emails)
            agent = random.choice(agent_ids) if random.random() < 0.5 else None
            start = dep_dt - timedelta(days=120)
            end = min(dep_dt, now_dt)
            if start > end:
                start, end = now_dt - timedelta(days=30), now_dt
            delta_days = (end - start).days or 1
            pdate = start + timedelta(days=random.randint(0, delta_days))
            up(conn, "INSERT OR IGNORE INTO purchases(ticket_id, customer_email, booking_agent_id, purchase_date) VALUES (?,?,?,?)",
               (tid, cust, agent, pdate.date().isoformat()))
            purchases_inserted += 1

    conn.commit()
    conn.close()
    print(f"Initialized SQLite DB at: {DB}")
    print(f"Flights: {len(flights_to_insert)}, Tickets: {tickets_inserted}, Purchases: {purchases_inserted}")
    print("Demo logins:")
    print("  Customer  → customer@demo.com / demo1234")
    print("  Agent     → booking@demo.com  / demo1234")
    print("  Staff(AA) → airlinestaff@demo.com / demo1234 (Admin+Operator)")
    print("  Staff(CE) → operator@demo.com / demo1234 (Operator)")

if __name__ == '__main__':
    main()
