# openknob

ASUS Dial daemon for Linux/GNOME.

## Quick start

Run the daemon:

```bash
sudo python3 -m openknob.daemon --device /dev/hidraw1 --socket /tmp/openknob.sock
```

Then connect a client to `/tmp/openknob.sock` to receive events like `rotate_cw`, `rotate_ccw`, and `press`.

Run the overlay with:

```bash
python3 -m openknob.overlay --socket /tmp/openknob.sock
```
