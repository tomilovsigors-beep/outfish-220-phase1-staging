# Owner-approved database-free runtime. No historical discovery at interpreter boot.
import os
os.environ.pop('DATABASE_URL',None)
