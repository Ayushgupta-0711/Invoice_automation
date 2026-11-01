
import getpass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# MySQL credentials
username = input("Enter MySQL username: ")
password = getpass.getpass("Enter MySQL password: ")

# Connection without selecting a database
server_url = URL.create(
    drivername="mysql+pymysql",
    username=username,
    password=password,
    host="127.0.0.1",
    port=3306
)

try:
    engine = create_engine(server_url)

    with engine.connect() as connection:
        version = connection.execute(
            text("SELECT VERSION()")
        ).scalar()

        print("\nMySQL Server Version:", version)

        connection.execute(
            text("CREATE DATABASE IF NOT EXISTS invoice_automation")
        )

        print("Database verified successfully!")

    engine.dispose()

    # Connect to project database
    database_url = URL.create(
        drivername="mysql+pymysql",
        username=username,
        password=password,
        host="127.0.0.1",
        port=3306,
        database="invoice_automation"
    )

    engine = create_engine(database_url)

    with engine.connect() as connection:
        database = connection.execute(
            text("SELECT DATABASE()")
        ).scalar()

        print("Connected Database:", database)
        print("Python and MySQL connection successful!")

    engine.dispose()

except Exception as error:
    print("\nConnection failed!")
    print("Error:", error)
