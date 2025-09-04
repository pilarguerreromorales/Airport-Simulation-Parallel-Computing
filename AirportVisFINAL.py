import random
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from queue import Queue
from datetime import timedelta
import csv
import pandas as pd
import pygame
import math


# Create a global print lock so different threads don't overload the command line
print_lock = threading.Lock()

#Visualisation stuff 
#
#to collect data for visualisation  we get a dictionary with passenger id as key then have flight, time and gate (and any additonal info from a passenger), to upgrade the visual
#Figuring out where to append to these lists was the hardest part ngl #####################################################################################
passenger_flight_list = []
gates_for_flights = []
all_passengers = []
###########################################################################################################################################################


#### INITITALISE SQL CONNECTION TO BE ABLE TO INSERT DATA AS SIMULATION OCCURS
import mysql.connector  # Importing the MySQL connector library
# # Establishing the database connection
cnx = mysql.connector.connect(user='root', password='Pilarmora10!', host='localhost')
mycursor = cnx.cursor()
lock_for_cursor = threading.Lock()

# # Creating the database if it doesn't exist
mycursor.execute("CREATE DATABASE IF NOT EXISTS mydatabase")
mycursor.execute("USE mydatabase")  # Switch to the created database

# # all passenegrs of simulatons stored in table so for different simulations we wil have a
# # primary key, in different siulations more tha one passenger may have same passenger id but
# # will not have same passneger num as this is automatically handled when inserting new
# # passenegers so that it is unique
mycursor.execute("""CREATE TABLE IF NOT EXISTS Passengers_Sim ( passenger_num INT PRIMARY KEY AUTO_INCREMENT, 
      passenger_id INT,
     preference VARCHAR(50),             
     assigned_seat VARCHAR(50),          
     booked_flight VARCHAR(50),         
     passed_security BOOLEAN DEFAULT FALSE, 
     arrival_time DATETIME DEFAULT NULL,             
     joined_security DATETIME DEFAULT NULL,           
     joined_boarding_queue DATETIME DEFAULT NULL,    
     booked_flight_time DATETIME DEFAULT NULL       
 );
 """)


# define a Seat class that will represent the seats in the flights
class Seat:
    def __init__(self, flight, seat_id, preference):
        self.flight = flight #the flight the seat belongs to
        self.seat_id = seat_id #the identifier of the seat
        self.lock = threading.Lock()  # Lock that will manage the seat only being accessed and assigned to one passengers
        # otherwise we would have lots of passengers booking them at the same time
        self.preference = preference # passengers seating preference

#function to check whether there is any available seat (not locked/reserved)
    def check_availability(self):
        return not self.lock.locked()

# function that releases reserved seats
    def release(self):
      # check whether the seat is reserved
        if self.lock.locked():
            self.lock.release() #releases the lock on the seat
            return True #True only if the seat was successfully released
        return False #this means the seat was unsuccessfully released

# function to reserve a seat
    def reserve(self):
        return self.lock.acquire() #attempt to acquire the lock to book the seat

# Class that represents a flight and manage its operations
class Flight:
    def __init__(self, flight_id, destination, scheduled_departure, fast_clock, Airport, total_seats=50):
        self.lock = threading.Lock()  # Flight-level lock that will synchronize operations
        self.flight_id = flight_id #unique identifier for each flight
        self.airport = Airport # reference to airport the flight belongs to
        self.destination = destination #destination of the flight
        self.scheduled_departure = scheduled_departure #the actual departure time which includes delays
        self.actual_departure_time = None  # To store actual departure time
        self.delay = None  # To store delay in minutes
        self.total_seats = total_seats # total number of seats on the flight
        self.list_seats = [Seat(self, seat_id, "business" if seat_id <= 20 else "economy") for seat_id in range(1, total_seats + 1)]
        self.passengers_arrived = [] # list of passengers that have arrived at the gate
        self.passengers_booked = [] # list of passengers who have booked seats
        self.fast_clock = fast_clock  # instance of a fast clock that simulates time
        self.in_airport = True # tells us if the flight is still at the airport
        self.gate = None # gate assigned to the flight
        self.boarding_started = False  # Track whether boarding has started
        self.delay_lock = threading.Lock()  # Lock to synchronize delay assignment

# function to assign seats to passengers
    def assign_seat(self, passenger):
        with self.lock: # ensure thread safety
            # Filter seats based on passenger preference and availibility
            available_seats = [seat for seat in self.list_seats if seat.preference == passenger.preference and seat.check_availability()]

            if available_seats:
                seat = random.choice(available_seats) # randomly  select an available seat
                seat.reserve()  # Reserve the seat
                self.passengers_booked.append(passenger)  # Add the passenger to the booked passengers
                return seat # return the assigned seat
            return None # return none if no availability

# function to assign gate to the flight
    def assign_gate(self):
        if self.gate is not None: # check whether a gate is already assigned
            return  # Exit if the gate has already been assigned

        with print_lock:
            print(f"Trying to assign gate for Flight {self.flight_id}...")
        while self.fast_clock.running: # continuously attempt to assign a gate
            available_gates = [gate for gate in self.airport.gates.values() if not gate.is_gate_assigned()] # list of the gates that are not assigned to any flight

            if available_gates:
                gate = available_gates[0] # select the first available gate
                gate.assign_flight(self)  # Assign this flight to the gate
                self.gate = gate  # Store the gate assignment
                return gate # return the assigned gate
            else:
                return None # return none if no gates are available

# function that simulates the flight operations
    def run_flight(self):
        # Simulate random delay (up to 30 minutes)
        delay_minutes = random.randint(0, 30)
        with self.delay_lock: # ensure thread safety
            self.delay = delay_minutes # set the dealy for the flight
            self.actual_departure_time = self.scheduled_departure + timedelta(minutes=self.delay) #calculate the actual departure time based on the delay

        while self.fast_clock.running: # continuosly simulate the flight
            current_time = self.fast_clock.get_current_time() #get thee current simualated time

            # Assign gate only once when the time is within the right window so one hour before departure
            if current_time >= (self.scheduled_departure - timedelta(hours=1)) and self.gate is None:
                if not self.boarding_started:  # Only assign gate once
                    with print_lock:
                        print(f"Flight {self.flight_id} is approaching the gate assignment time at {current_time.strftime('%H:%M:%S')}")
                    gate = self.assign_gate() # attempt to assign a gate
                    if gate is None: # try again if no gate is available
                        with print_lock:
                            print(f"No available gates for Flight {self.flight_id}, retrying...")
                        time.sleep(1)  # wait before trying again

            # Start boarding 30 minutes before actual departure
            if current_time >= (self.actual_departure_time - timedelta(minutes=30)) and not self.boarding_started:
                self.boarding_started = True  # Mark boarding as started
                if self.gate:
                    self.gate.board_passengers()  # Begin boarding passengers

            # simulate departure when the actual departure time has been reached
            if current_time >= self.actual_departure_time:
                with print_lock: #synchronize
                    print(f"Flight {self.flight_id} is departing at {current_time.strftime('%H:%M:%S')} with {len(self.passengers_arrived)} passengers on board.")
                with self.lock: #thread safety when changing flight state
                    self.in_airport = False # marks the flight as departed
                break  # Exit loop after departure

            # Sleep briefly to prevent tight loop
            time.sleep(0.1)

# Gate class that represents the gates and its operations
class Gate:
    def __init__(self, gate_id):
        self.gate_id = gate_id # unique identifier for each gate
        self.lock = threading.Lock() # lock to synchronize
        self.boarding_queue_open = False  # boarding queue is initially closed
        self.boarding_queue = Queue()  # queue for boarding passengers
        self.flight = None  # initially no flight assigned to this gate
        self.gate_assigned = False # tell us if the gate is currently assigned to a flight

# function to check if the gate is assigned to a flight
    def is_gate_assigned(self):
        with self.lock: # thread safety when checking gate status
            return self.gate_assigned #indicates whether the gate is assigned
# function to assign flight to a gate
    def assign_flight(self, flight):
        global gates_for_flights
        with self.lock: #thread safety
            if not self.gate_assigned:  # ensure that the gate is not already assigned
                self.flight = flight # assign the flight to the gate
                self.gate_assigned = True  # Mark the gate as assigned
                self.boarding_queue_open = True  # Open the boarding queue for the flight
                with print_lock:
                    print(f"Gate {self.gate_id} assigned to Flight {flight.flight_id}.")
                    gates_for_flights.append((self.gate_id, flight.flight_id))

# function to board passengers on the flight
    def board_passengers(self):
        with self.lock: # thread safety use the gate's lock so passengers cant asscess the queue anymore and everything run's concurrently.
            self.boarding_queue_open = False  # when boarding passengers, we close the boarding queue
            with print_lock:
                print(f"Gate {self.gate_id} is boarding passengers for Flight {self.flight.flight_id}.")

            # board each passenger from the boarding queue to the flight's passengers list
            while not self.boarding_queue.empty():  # check if the queue is not empty
                passenger = self.boarding_queue.get()  # get the next passenger in the queue
                self.flight.passengers_arrived.append(passenger)  # add to flight's passenger list
                with print_lock:
                    print(f"Passenger {passenger.passenger_id} has boarded Flight {self.flight.flight_id} at Gate {self.gate_id}.")
            self.flight.gate = None # remove the flights reference from the gate
            self.gate_assigned = False # mark the gate as empty
            self.flight = None # clear  the flight assignment from the gate

# class that represents the passengers and their behavior
class Passenger:
    def __init__(self, passenger_id, preference, airport, clock):
        self.passenger_id = passenger_id # unique identifier
        self.preference = preference # seat preference
        self.assigned_seat = None # seat assigned to the passenger
        self.airport = airport  # Reference to the airport to access available flights
        self.clock = clock  # Use the FastClock instance
        self.booked_flight = None  # To store the chosen flight
        self.passed_security = False # indicate whether passenger has passed security

        # attributes to store information and later put it in sql table
        self.arrival_time = None
        self.joined_boarding_queue = None
        self.joined_security = None
        self.booked_flight_time = None


    def insert_in_db(self):
        with lock_for_cursor:
            insert_query = """
                       INSERT INTO Passengers_Sim (
                           passenger_id, preference, assigned_seat, booked_flight, passed_security, 
                           arrival_time, joined_security, joined_boarding_queue, 
                           booked_flight_time
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       """
            try:
                mycursor.execute(insert_query, (
                    self.passenger_id,
                    self.preference,
                    self.assigned_seat.seat_id if self.assigned_seat else None,
                    self.booked_flight.flight_id if self.booked_flight else None,
                    self.passed_security,
                    self.arrival_time if self.arrival_time else None,
                    self.joined_security if self.joined_security else None,
                    self.joined_boarding_queue if self.joined_boarding_queue else None,
                    self.booked_flight_time if self.booked_flight_time else None
                ))
                cnx.commit()
            except mysql.connector.Error as err:
                with print_lock:
                    print(f"Error: {err}")



    #function to define the passenger's actions
    def run(self):
        # Execute the passenger behaviors
        self.random_delay_before_booking() #simulate a delay before booking a flight
        self.book_flight() # attempt to book a flight
        self.arrive_at_airport() # simulate arrival at the airport
        self.join_security_queue() # join the security queue
        while self.clock.running: # continuosly check if security has been passed
            if self.passed_security:
                self.join_boarding_queue() # join the boarding queue if security has been passed
                self.insert_in_db()
                break # Once in boarding qeue passengers ids handled by the flight (boarded etc) but they do not have any
                # more behaviour to simulate so just exit the thread.
            else:
                continue # keep checking until security is passed

   # function that simulates a random delay before the passenger decides to book their flight
    def random_delay_before_booking(self):
        booking_delay = random.randint(1, 20*60)  # delay up to 20 hours
        target_time = self.clock.get_current_time() + datetime.timedelta(minutes=booking_delay)
#wait until target time is reached
        while self.clock.get_current_time() < target_time and self.clock.running:
            time.sleep(1)  # sleep for 1 second between the checks

# function that attempts to book flight based on the availibilities
    def book_flight(self):
        while not self.booked_flight and self.clock.running:
          # filter flights based on availibility and departure time
            available_flights = [
                flight for flight in self.airport.flights
                if flight.in_airport and (
                            flight.scheduled_departure > self.clock.get_current_time() + datetime.timedelta(hours=2))
            ]

            if available_flights:
                self.booked_flight = random.choice(available_flights) #choose a random flight
            else:
                with print_lock:
                    print(f"Passenger {self.passenger_id} found no available flights.")
                time.sleep(5) # wait and retry if no available flights
                continue

            # Attempt to assign a seat on the booked flight
            seat = self.booked_flight.assign_seat(self)
            if seat:
                self.assigned_seat = seat # store the assigned seat
                self.booked_flight_time = self.clock.get_current_time()
                with print_lock:
                    print(f"Passenger {self.passenger_id} has booked flight {self.booked_flight.flight_id}. Seat: {seat.seat_id}.")
            else:
                self.booked_flight = None # reset if no seat is available

    def random_delay_before_arrival(self):
        # Arrival is based on the flight's departure time (randomized between 45 to 75 minutes before departure)
        if self.booked_flight:
            arrival_offset = random.randint(45, 75)  # Randomize arrival time before flight departure
            arrival_time = self.booked_flight.scheduled_departure - datetime.timedelta(minutes=arrival_offset)

            # Wait until the arrival time
            while self.clock.get_current_time() < arrival_time and self.clock.running:
                time.sleep(1)  # sleep for 1 second between checks

# simulate the passenger's arrival at the airport
    def arrive_at_airport(self):
        # calculate arrival time (randomized between 45 minutes and 75 minutes before the scheduled departure)
        arrival_offset = random.randint(45, 75)  # randomly choose between 45 and 75
        arrival_time = self.booked_flight.scheduled_departure - datetime.timedelta(minutes=arrival_offset)

        # simulate time until the passenger arrives at the airport
        while self.clock.running:
            current_time = self.clock.get_current_time()
            self.arrival_time = self.clock.get_current_time()
            if current_time >= arrival_time:  # Retrieve the current time from FastClock
                with print_lock:
                    print(f"Passenger {self.passenger_id} has arrived at the airport.")
                break # exit the loop once the passenger arrives
            time.sleep(1)  # Check every second

# add passengers in the security queue
    def join_security_queue(self):
        with print_lock:
            print(f"Passenger {self.passenger_id} joined the security queue of the airport.")
        with self.airport.security_queue_lock: # thread safety
            self.airport.security_queue.put(self) # add passenger to the security queue
        self.joined_security= self.clock.get_current_time()

    # attempts to place the passenger in the boarding queue for the flight.
    def join_boarding_queue(self):
        while self.clock.running:
            current_time = self.clock.get_current_time()

            # check if the flight has already departed
            if current_time >= self.booked_flight.actual_departure_time:
                with print_lock:
                    print(
                        f"Passenger {self.passenger_id} cannot join the boarding queue for {self.booked_flight.flight_id} as it has already departed.")
                return  # xxit if flight has departed

            # check if the boarding queue is open
            if self.booked_flight.gate and self.booked_flight.gate.boarding_queue_open:
                # add to boarding queue after arriving
                self.booked_flight.gate.boarding_queue.put(self)
                self.joined_boarding_queue = self.clock.get_current_time()
                with print_lock:
                    print(
                        f"Passenger {self.passenger_id} has joined the boarding queue for flight {self.booked_flight.flight_id}.")
                break  # xxit after successfully joining the queue
            else:
                time.sleep(1)  # check again in 1 second if the boarding queue is closed

# Creat a fast clock to simulate that time is happening faster
class FastClock:
    def __init__(self):
        self.current_time = datetime.datetime.now()  # initialize current time to the current date and time.
        self.start_time = self.current_time  # record the simulation start time
        self.stop_time = self.start_time + datetime.timedelta(hours=24)  # simulation runs for 24 hours
        self.running = True  # flag to control the clock's running state.

    def run(self):
        while self.running:
            time.sleep(1)  # simulate 10 minutes passing for every 1 second of real-time sleep
            self.increment_time() # increment the simulated time
            # check if 24 hours have passed
            if self.current_time >= self.stop_time:
                with print_lock:
                    self.running = False # stop the clock
                print("FastClock: Simulation has reached 24 hours and will stop.")
                break  # exit the clock thread

# increment the current time by 10 minutes
    def increment_time(self):
        self.current_time += datetime.timedelta(minutes=10) # add 10 minutes to the current time
        print(f"FastClock: Current Time = {self.current_time.strftime('%Y-%m-%d %H:%M:%S')}") # display the updated time

# retrieve the current simulated time
    def get_current_time(self):
        return self.current_time # return the current time of the simulation

# stop the clock
    def stop(self):
        self.running = False # set the running flag to False, which stops the simulation

# class that defines the security staff and their operations
class Security_Workers:
    def __init__(self, id, airport):
        self.id = id # unique identifier
        self.airport = airport # reference to the airport to access the security queue

# function that processes passengers through the security
    def process_security(self):
        while self.airport.clock.running: # continue processing passengers while the simulation is running
            with self.airport.security_queue_lock: # thread safety
                if not self.airport.security_queue.empty(): # check whether the security queue is empty
                    passenger = self.airport.security_queue.get() # get the next passenger from the queue
                    passenger.passed_security = True # mark the passenger as having passed security
                    print(f"Security worker {self.id} processed passenger {passenger.passenger_id}")
            time.sleep(0.1) # delay to avoid tight loop

class Airport:
    def __init__(self, name):
        self.name = name
        self.flights = []  # list of all flights at the airport
        self.gates = {letter: Gate(letter) for letter in ['A', 'B', 'C', 'D', 'E']} # dictionary of the gates
        self.clock = FastClock()  # the fast clock that will keep track of time
        self.security_queue = Queue() # queue for passengers waiting at security
        self.lock = threading.Lock()  # lock for the airport to manage flights
        self.security_queue_lock = threading.Lock() # thread safety
        self.security_workers = [Security_Workers(id, self) for id in range(1, 10)]

# function to add flights to the airport
    def add_flight(self, flight_id, destination, scheduled_departure):
        with self.lock:  # Lock the airport while adding a flight
            flight = Flight(flight_id, destination, scheduled_departure, fast_clock=self.clock, Airport=self) # create a new flight object and add it to the list of flights
            self.flights.append(flight) # add the flight to the list
            with print_lock:
                print(f"Scheduled {flight_id} to {destination} at {scheduled_departure.strftime('%Y-%m-%d %H:%M')}.")

# function that randomizes and schedules the flights
    def randomize_flights(self, num_flights):
        destinations = ["New York", "Los Angeles", "London", "Paris", "Tokyo", "Sydney"] # list of possible destinations
        current_time = self.clock.get_current_time() # get the current simulated time

        for i in range(num_flights):
            flight_id = f"Flight-{random.randint(100, 999)}" # generate a random flight id
            destination = random.choice(destinations) # randomly choose a destination

            # randomize departure time within the next 2 to 24 hours
            # we do this so that flights are not too early on in the simulation otherwise no passenger will have booked them
            departure_offset = random.randint(2*60, 24*60)  # offset in minutes (24 hours = 1440 minutes)
            scheduled_departure = current_time + timedelta(minutes=departure_offset)

            self.add_flight(flight_id, destination, scheduled_departure) # schedule the flight

# function that simulate day's operation in the airport by executing all of the threads usinga threadpool
    def simulate_day(self):
        with print_lock:
            print(f"Starting daily simulation at airport: {self.name}...")

        # use ThreadPoolExecutor for concurrent simulation
        with ThreadPoolExecutor(max_workers=221) as executor:
            # 221 (200 passengers + 10 flights + 10 security workers + 1 fast clock)
            # start the clock
            clock_future = executor.submit(self.clock.run)

            # start flight threads
            flight_futures = []
            for flight in self.flights:
                with print_lock:
                    print(f"Starting thread for {flight.flight_id}.")
                future = executor.submit(flight.run_flight)
                flight_futures.append(future)

            # generate and start passenger threads
            for i in range(200):
                preference = random.choice(["business", "economy"])
                passenger = Passenger(i + 1, preference, self, self.clock)  # pass the airport and clock to passengers
                all_passengers.append(passenger)
                executor.submit(passenger.run) # submit the passenger thread for execution

            # start security worker threads
            for worker in self.security_workers:
                executor.submit(worker.process_security)

            # wait for the clock to finish
            clock_future.result()

            # wait for all flights to finish
            for future in flight_futures:
                future.result()

            # once the clock has stopped, we collect data and write the CSV file
            with print_lock:
                print("Simulation completed. Generating CSV report...")
            self.generate_csv_report()

    def visualize_airport(self):
        for passenger in all_passengers:
            if passenger.booked_flight:  #taking flight ids from all passengers list which is a bunch of passenger objects, 
                flight_id = passenger.booked_flight.flight_id
                passenger_flight_list.append((passenger.passenger_id, flight_id))
            else:
                passenger_flight_list.append((passenger.passenger_id, None))  # No flight assigned

        flight_gate_map = {flight: gate for gate, flight in gates_for_flights}
        passenger_gate_map = {p_id: flight_gate_map.get(flight) for p_id, flight in passenger_flight_list}

        # Group passengers by gate
        from collections import defaultdict
        passengers_by_gate = defaultdict(list)
        for passenger_id, gate_id in passenger_gate_map.items():
            if gate_id is not None:
                passengers_by_gate[gate_id].append(passenger_id)

        # Pygame setup
        pygame.init()
        screen = pygame.display.set_mode((1000, 1000))
        pygame.display.set_caption("Airport Simulation")
        clock = pygame.time.Clock()

        # Gate positions
        gates = {
            'A': (200, 100),
            'B': (200, 300),
            'C': (550, 100),
            'D': (800, 100),
            'E': (800, 300),
        }

        # Initialize passenger positions and speeds
        passenger_positions = {}
        passenger_speeds = {}

        # Sequentially load passengers per gate
        for gate_id, passenger_ids in passengers_by_gate.items():
            print(f"Loading passengers for Gate {gate_id}")

            # Initialize passengers for this gate
            for passenger_id in passenger_ids:
                passenger_positions[passenger_id] = [random.randint(50, 750), random.randint(400, 550)]
                passenger_speeds[passenger_id] = random.uniform(1, 2)

            # Animate passengers moving toward the gate
            gate_x, gate_y = gates[gate_id]
            all_reached = False
            while not all_reached:
                all_reached = True
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        pygame.quit()
                        return

                # Clear screen
                screen.fill((255, 255, 255))  # White background

                # Render the title for the current gate being boarded
                title_text = f"Now Boarding Gate {gate_id}"
                title_surface = pygame.font.Font(None, 36).render(title_text, True, (0, 0, 0))  # Black text
                screen.blit(title_surface, (600, 20))  # Top-right corner

                # Draw gates
                for g_id, (x, y) in gates.items():
                    pygame.draw.circle(screen, (0, 255, 0), (x, y), 20)  # Green gates
                    text = pygame.font.Font(None, 24).render(g_id, True, (0, 0, 0))  # Black gate labels
                    screen.blit(text, (x - 10, y - 30))

                # Move and draw passengers
                for passenger_id in passenger_ids:
                    position = passenger_positions[passenger_id]
                    dx, dy = gate_x - position[0], gate_y - position[1]
                    distance = math.hypot(dx, dy)

                    if distance > 1:  # Continue moving if not at the gate
                        all_reached = False
                        position[0] += dx / distance * passenger_speeds[passenger_id]
                        position[1] += dy / distance * passenger_speeds[passenger_id]

                    # Draw passenger as a blue dot
                    pygame.draw.circle(screen, (0, 0, 255), (int(position[0]), int(position[1])), 5)
                    text = pygame.font.Font(None, 16).render(str(passenger_id), True, (0, 0, 0))  # Black text
                    screen.blit(text, (int(position[0]) - 5, int(position[1]) - 15))

                # Update the display
                pygame.display.flip()
                clock.tick(30)

        pygame.quit()



    def generate_csv_report(self):
        # collect flight data, this is not generated with sql this is just to visualize the flight data
        # for sql we only used a tabl for the passengers
        flight_data = []
        for flight in self.flights:
            flight_id = flight.flight_id
            scheduled_departure = flight.scheduled_departure.strftime('%Y-%m-%d %H:%M')
            actual_departure = flight.actual_departure_time.strftime('%Y-%m-%d %H:%M') if flight.actual_departure_time else "N/A"
            delay = flight.delay if flight.delay is not None else 0
            boarded_passengers = len(flight.passengers_arrived)
            missed_passengers = len(flight.passengers_booked) - boarded_passengers
            flight_data.append({
                'Flight ID': flight_id,
                'Scheduled Departure': scheduled_departure,
                'Actual Departure': actual_departure,
                'Delay (minutes)': delay,
                'Passengers Boarded': boarded_passengers,
                'Passengers Missed': missed_passengers
            }) # append the collected data

        # write to CSV file
        csv_filename = f"flight_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(csv_filename, mode='w', newline='') as csv_file:
            fieldnames = ['Flight ID', 'Scheduled Departure', 'Actual Departure', 'Delay (minutes)', 'Passengers Boarded', 'Passengers Missed']
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)

            writer.writeheader() # write the csv header
            for data in flight_data:
                writer.writerow(data) # write each row of the flight data

        print(f"CSV report generated: {csv_filename}") # notify that the CSV report is generated

if __name__ == "__main__":
    airport = Airport("International Airport") # create an airport instance

    # generate 10 random flights
    airport.randomize_flights(10)

    # start the simulation for the day
    airport.simulate_day()

    #visual
    airport.visualize_airport()

    # Fetch all data from Passengers table in sql and convert into csv
    query = "SELECT * FROM Passengers_Sim"
    mycursor.execute(query)
    columns = [desc[0] for desc in mycursor.description]
    data = mycursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    mycursor.close()
    cnx.close()

    # Save the DataFrame to a CSV file
    df.to_csv('passengers_data.csv', index=False)  # Save without index
    print("Passenger data has been saved to 'passengers_data.csv'.")
