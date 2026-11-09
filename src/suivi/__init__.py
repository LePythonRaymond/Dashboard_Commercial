"""
Suivi de production: the accounting's monthly production file, rebuilt from Furious every night.

Today the accountant pastes five Furious exports (projects, purchases, invoices, time, budget lines)
into an Excel file and fills a per-project table by hand. This package reads the same data straight
from the Furious API, so the file can be built without any paste, and checks every read: a source
that fails, comes back empty or shrinks without reason stops the run loudly instead of producing
a quietly wrong file.
"""
