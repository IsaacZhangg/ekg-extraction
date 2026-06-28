# EKG PDF to CSV

Browser-based tool for extracting ECG samples from vector-based EKG PDFs and downloading the results as CSV.

## Use the Web App

Open the GitHub Pages site, choose or drop an EKG PDF, then click **Extract data**. The page reads vector path data from the PDF in your browser and prepares a CSV download.

PDF files are processed locally in the browser. They are not uploaded to a server by this static GitHub Pages app.

## What It Produces

The exported CSV includes:

- sample index
- elapsed time
- row and row-local sample index
- original PDF coordinates
- row baseline
- estimated millivolts

The page also reports extraction metadata such as sample count, sample rate, duration, source pages, and ECG-derived rhythm measurements.

## PDF Requirements

This tool is designed for vector-based EKG PDFs where the ECG trace is present as PDF drawing/path data. Scanned PDFs or image-only PDFs may not produce usable results.

The browser extractor scans every page for compatible ECG trace rows, then concatenates those rows into one continuous sample stream. This supports short single-page recordings such as 30-second PDFs and longer multi-page recordings such as 1-5 minute PDFs when they use the same vector layout.

The conversion assumes standard ECG paper calibration:

- 25 mm/s
- 10 mm/mV

## Optional Python Extractor

The repository also includes `extract_ecg_from_pdf.py`, a command-line extractor that uses `qpdf` to parse PDF vector content and write CSV plus metadata files.

```bash
uvx ruff check extract_ecg_from_pdf.py
uvx ty check extract_ecg_from_pdf.py
python extract_ecg_from_pdf.py path/to/ekg.pdf
```

Install `qpdf` before using the Python extractor.

```bash
brew install qpdf
```

## Deploying to GitHub Pages

The static app is served from `index.html`. In the GitHub repository settings, enable Pages with:

- Source: **Deploy from a branch**
- Branch: `main`
- Folder: `/ (root)`

GitHub Pages will publish the app at:

```text
https://IsaacZhangg.github.io/ekg-extraction/
```
