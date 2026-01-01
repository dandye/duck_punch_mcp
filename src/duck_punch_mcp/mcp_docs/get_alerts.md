Retrieve alerts from Chronicle within a specified time range.

This tool allows you to search for alerts using a 'snapshot' query (filtering on fields like status, priority, etc.) and an optional 'baseline' query.

**Important Usage Details:**
- `start_time` and `end_time` are required and must be Python datetime objects.
- `snapshot_query` defaults to excluding closed alerts (`feedback_summary.status != "CLOSED"`).
- Use `max_alerts` to control the volume of returned data (default 1000).

**Example:**
To find all open 'High' priority alerts in the last 24 hours:
- `start_time`: (24 hours ago)
- `end_time`: (now)
- `snapshot_query`: 'feedback_summary.priority = "HIGH" AND feedback_summary.status = "OPEN"'
