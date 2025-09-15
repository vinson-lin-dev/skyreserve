PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS airline ( airline_name TEXT PRIMARY KEY );

CREATE TABLE IF NOT EXISTS airline_staff (
  username TEXT PRIMARY KEY,
  password TEXT NOT NULL,
  first_name TEXT,
  last_name TEXT,
  date_of_birth DATE,
  airline_name TEXT NOT NULL,
  FOREIGN KEY (airline_name) REFERENCES airline(airline_name)
);

CREATE TABLE IF NOT EXISTS airplane (
  airline_name TEXT NOT NULL,
  airplane_id INTEGER NOT NULL,
  seats INTEGER NOT NULL,
  PRIMARY KEY (airline_name, airplane_id),
  FOREIGN KEY (airline_name) REFERENCES airline(airline_name)
);

CREATE TABLE IF NOT EXISTS airport (
  airport_name TEXT PRIMARY KEY,
  airport_city TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS booking_agent (
  email TEXT PRIMARY KEY,
  password TEXT NOT NULL,
  booking_agent_id INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS booking_agent_work_for (
  email TEXT NOT NULL,
  airline_name TEXT NOT NULL,
  PRIMARY KEY (email, airline_name),
  FOREIGN KEY (email) REFERENCES booking_agent(email),
  FOREIGN KEY (airline_name) REFERENCES airline(airline_name)
);

CREATE TABLE IF NOT EXISTS customer (
  email TEXT PRIMARY KEY,
  name TEXT,
  password TEXT NOT NULL,
  building_number TEXT,
  street TEXT,
  city TEXT,
  state TEXT,
  phone_number TEXT,
  passport_number TEXT,
  passport_expiration DATE,
  passport_country TEXT,
  date_of_birth DATE
);

CREATE TABLE IF NOT EXISTS flight (
  airline_name TEXT NOT NULL,
  flight_num INTEGER NOT NULL,
  departure_airport TEXT NOT NULL,
  departure_time TEXT NOT NULL,
  arrival_airport TEXT NOT NULL,
  arrival_time TEXT NOT NULL,
  price REAL NOT NULL,
  status TEXT NOT NULL,
  airplane_id INTEGER NOT NULL,
  PRIMARY KEY (airline_name, flight_num),
  FOREIGN KEY (airline_name) REFERENCES airline(airline_name),
  FOREIGN KEY (departure_airport) REFERENCES airport(airport_name),
  FOREIGN KEY (arrival_airport) REFERENCES airport(airport_name),
  FOREIGN KEY (airline_name, airplane_id) REFERENCES airplane(airline_name, airplane_id)
);

CREATE TABLE IF NOT EXISTS permission (
  username TEXT NOT NULL,
  permission_type TEXT NOT NULL,
  PRIMARY KEY (username, permission_type),
  FOREIGN KEY (username) REFERENCES airline_staff(username)
);

CREATE TABLE IF NOT EXISTS ticket (
  ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
  airline_name TEXT NOT NULL,
  flight_num INTEGER NOT NULL,
  FOREIGN KEY (airline_name, flight_num) REFERENCES flight(airline_name, flight_num) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS purchases (
  ticket_id INTEGER NOT NULL,
  customer_email TEXT NOT NULL,
  booking_agent_id INTEGER,
  purchase_date DATE NOT NULL,
  PRIMARY KEY (ticket_id, customer_email),
  FOREIGN KEY (ticket_id) REFERENCES ticket(ticket_id) ON DELETE CASCADE,
  FOREIGN KEY (customer_email) REFERENCES customer(email),
  FOREIGN KEY (booking_agent_id) REFERENCES booking_agent(booking_agent_id)
);

CREATE INDEX IF NOT EXISTS idx_flight_departure_time ON flight(departure_time);
CREATE INDEX IF NOT EXISTS idx_flight_departure_arrival ON flight(departure_airport, arrival_airport);
CREATE INDEX IF NOT EXISTS idx_ticket_flight ON ticket(airline_name, flight_num);
CREATE INDEX IF NOT EXISTS idx_purchases_date ON purchases(purchase_date);
