# Owner-approved database-free runtime, 2026-10-23.
# No historical startup jobs or DB reads/writes. One instance, one worker.
workers=1
threads=4
timeout=120

def on_starting(server):
    import os
    os.environ.pop('DATABASE_URL',None)
    server.log.info('POSTGRES_RETIRED_CREATE_LOG_IS_MASTER')
