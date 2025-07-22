import asyncio

RAW_DATA_QUEUE = asyncio.Queue()
LOGGING_QUEUE = asyncio.Queue()
SEGMENT_QUEUE = asyncio.Queue(maxsize=10)
RESULT_QUEUE = asyncio.Queue(maxsize=10)

sensor_websockets = set()
app_websockets = set()
debug_websockets = set()

is_processing_active = False

log_file_handler = None
log_file_path = None
is_first_entry_in_file = True