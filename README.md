# Passport Photo Tool

A small desktop app that helps you edit your photo a regular photo to create a passport-size photo for Indian passport application. It removes the background, evens the lighting, detects your face, and crops to the required 630x810 px dimensions.

## Install

### macOS

Open Terminal (Spotlight → type "Terminal" → Enter) and paste:

```sh
curl -LsSf https://raw.githubusercontent.com/smitkiri/passport-photo-tool/main/install.sh | sh
```

This downloads the latest release, installs it to `~/Downloads`, strips the macOS quarantine flag (so Gatekeeper doesn't block the unsigned app), and launches it.

To update, re-run the same command.

**Why is this needed?** The app isn't signed with an Apple Developer ID (that costs me $99/year), so double-clicking the downloaded `.app` shows a "damaged" or "unidentified developer" dialog. Stripping the quarantine attribute is the standard workaround and doesn't require paying Apple.

**Want to inspect the script before running it?** You should. View it here: [install.sh](./install.sh).

### Windows

Download `PassportPhotoTool-Windows.zip` from the [latest release](https://github.com/smitkiri/passport-photo-tool/releases/latest), unzip it, and run `PassportPhotoTool.exe`.

## Troubleshooting (macOS)

**"PassportPhotoTool is damaged and can't be opened"** after installing manually (not via the script): the quarantine attribute wasn't removed. Run:

```sh
xattr -cr ~/Downloads/PassportPhotoTool.app
```

**Installer says "Intel Macs are not currently supported"**: only Apple Silicon (arm64) builds are published. If you need an Intel build, please open an issue.

**Can't find the app after install**: it's in `~/Downloads`.

## Development

```sh
uv sync
uv run main.py
```

### Running in a browser

To use your regular browser instead of the desktop window:

```sh
uv run main.py --browser
```

This starts the local server and opens the app at http://127.0.0.1:8000 in your default browser. Stop it with Ctrl+C.

The server uses port 8000 by default. If that port is busy (for example, the installed app is open), set `PORT`:

```sh
PORT=8123 uv run main.py --browser
```

`PORT` also works without `--browser` for the desktop window.

### Building

```sh
make build-mac        # macOS .app bundle
make build-windows    # Windows .exe directory
```

Releases are built automatically by GitHub Actions on pushing a `v*` tag.
