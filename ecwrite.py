EC_IO_FILE = '/sys/kernel/debug/ec/ec0/io'

from subprocess import run


def ensure_ec_access():
    try:
        with open(EC_IO_FILE, "rb"):
            return True
    except PermissionError:
        print("Permission denied for", EC_IO_FILE)
        print("Launch using the privileged wrapper (pkexec) or as root.")
        return False
    except FileNotFoundError:
        print(EC_IO_FILE, "not found. Trying to load ec_sys module...")
        result = run(["modprobe", "ec_sys", "write_support=1"], check=False)
        if result.returncode != 0:
            print("Failed to load ec_sys. Enable the module and debugfs, then retry.")
            return False
        try:
            with open(EC_IO_FILE, "rb"):
                print("ec_sys loaded successfully.")
                return True
        except OSError as exc:
            print("EC I/O path still unavailable:", exc)
            return False
    except OSError as exc:
        print("Unable to access EC I/O path:", exc)
        return False


def ec_write(address, value):
    with open(EC_IO_FILE, "rb+") as f:
        f.seek(address)
        old_value = ord(f.read(1))
        if value != old_value:
            print("Before: %3d\nAfter: %3d" % (old_value, value))
            f.seek(address)
            f.write(bytearray([value]))
        else:
            print("Value was not changed: %3d" % value)


def ec_read(address):
    with open(EC_IO_FILE, "rb") as f:
        f.seek(address)
        return ord(f.read(1))
