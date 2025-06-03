from flask import Blueprint, request, json, render_template, Response
from dateutil.parser import parse
import pytz
import logging
from app.extensions import mongo

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

webhook_bp = Blueprint('webhook', __name__)

def format_utc(timestamp_str):
    if not timestamp_str:
        return "Unknown time"
    try:
        dt = parse(timestamp_str)
        dt_utc = dt.astimezone(pytz.utc)
        return dt_utc.strftime('%-d %B %Y - %-I:%M %p UTC')
    except Exception as e:
        logger.error(f"Error formatting timestamp {timestamp_str}: {e}")
        return "Invalid time"

def format_event_for_ui(event):
    timestamp = format_utc(event.get('timestamp'))
    action = event.get('action', 'UNKNOWN')
    author = event.get('author', 'Unknown')
    
    if action == 'PUSH':
        to_branch = event.get('to_branch', 'unknown')
        return f"{author} pushed to {to_branch} on {timestamp}"
    elif action == 'PULL_REQUEST':
        from_branch = event.get('from_branch', 'unknown')
        to_branch = event.get('to_branch', 'unknown')
        return f"{author} submitted a pull request from {from_branch} to {to_branch} on {timestamp}"
    elif action == 'MERGE':
        from_branch = event.get('from_branch', 'unknown')
        to_branch = event.get('to_branch', 'unknown')
        return f"{author} merged branch {from_branch} to {to_branch} on {timestamp}"
    else:
        return f"Unknown action: {action}"

@webhook_bp.route('/')
def index():
    try:
        events = list(mongo.db.events.find().sort('timestamp', -1).limit(10))
        logger.info(f"Retrieved {len(events)} events from database")
        return render_template('index.html', events=events)
    except Exception as e:
        logger.error(f"Database error in index: {e}")
        return f"Database error: {str(e)}", 500

@webhook_bp.route('/events', methods=['GET'])
def get_latest_events():
    try:
        events = mongo.db.events.find().sort('timestamp', -1).limit(10)
        result = [{'message': format_event_for_ui(event)} for event in events]
        pretty_json = json.dumps(result, indent=2)
        return Response(pretty_json, mimetype='application/json')
    except Exception as e:
        logger.error(f"Error getting events: {e}")
        error_json = json.dumps({'error': str(e)}, indent=2)
        return Response(error_json, status=500, mimetype='application/json')

@webhook_bp.route('/receiver', methods=['POST'])
def github_webhook():
    logger.info("=== Webhook received ===")
    
    # Log headers for debugging
    logger.info(f"Content-Type: {request.headers.get('Content-Type')}")
    logger.info(f"X-GitHub-Event: {request.headers.get('X-GitHub-Event')}")
    logger.info(f"User-Agent: {request.headers.get('User-Agent')}")
    
    if request.headers.get('Content-Type') == 'application/json':
        try:
            event_type = request.headers.get('X-GitHub-Event')
            data = request.get_json()
            
            logger.info(f"Event type: {event_type}")
            logger.info(f"Payload keys: {list(data.keys()) if data else 'No data'}")

            if event_type == 'push':
                # Check if head_commit exists
                if not data.get('head_commit'):
                    logger.warning("No head_commit in push event")
                    return json.dumps({'status': 'ignored', 'message': 'No head_commit'}), 200
                
                event_data = {
                    'request_id': data['head_commit']['id'],
                    'author': data['pusher']['name'],
                    'action': 'PUSH',
                    'from_branch': None,
                    'to_branch': data['ref'].split('/')[-1],
                    'timestamp': data['head_commit']['timestamp']
                }
                
            elif event_type == 'pull_request':
                pr = data['pull_request']
                pr_action = data.get('action')
                
                logger.info(f"PR action: {pr_action}")
                logger.info(f"PR merged: {pr.get('merged')}")
                logger.info(f"PR state: {pr.get('state')}")
                
                # Determine action type
                if pr_action == 'closed' and pr.get('merged'):
                    action_type = 'MERGE'
                    timestamp = pr.get('merged_at')
                else:
                    action_type = 'PULL_REQUEST'
                    timestamp = pr.get('created_at')
                
                # Check for required fields
                if not timestamp:
                    logger.warning(f"No timestamp for PR action {pr_action}")
                    timestamp = pr.get('updated_at') or pr.get('created_at')
                
                event_data = {
                    'request_id': str(pr['number']),
                    'author': pr['user']['login'],
                    'action': action_type,
                    'from_branch': pr['head']['ref'],
                    'to_branch': pr['base']['ref'],
                    'timestamp': timestamp
                }
                
            else:
                logger.info(f"Ignoring event type: {event_type}")
                return json.dumps({'status': 'ignored', 'message': 'Event type not handled'}), 200

            # Log the event data before insertion
            logger.info(f"Event data to insert: {event_data}")
            
            # Insert into database
            result = mongo.db.events.insert_one(event_data)
            logger.info(f"Inserted event with ID: {result.inserted_id}")
            
            # Verify insertion
            inserted_event = mongo.db.events.find_one({'_id': result.inserted_id})
            if inserted_event:
                logger.info(f"Verified insertion: {inserted_event}")
                print(format_event_for_ui(event_data))
            else:
                logger.error("Failed to verify insertion")
            
            return json.dumps({'status': 'success', 'inserted_id': str(result.inserted_id)}), 200

        except KeyError as e:
            logger.error(f"Missing key in payload: {e}")
            logger.error(f"Full payload: {json.dumps(data, indent=2) if 'data' in locals() else 'No data'}")
            return json.dumps({'status': 'error', 'message': f'Missing key: {str(e)}'}), 400
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return json.dumps({'status': 'error', 'message': str(e)}), 500

    logger.warning(f"Invalid Content-Type: {request.headers.get('Content-Type')}")
    return json.dumps({'status': 'error', 'message': 'Invalid Content-Type'}), 400


# Additional debugging route
@webhook_bp.route('/debug/events', methods=['GET'])
def debug_events():
    """Debug route to see all events in the database"""
    try:
        events = list(mongo.db.events.find().sort('timestamp', -1))
        return json.dumps([{
            'id': str(event['_id']),
            'request_id': event.get('request_id'),
            'author': event.get('author'),
            'action': event.get('action'),
            'from_branch': event.get('from_branch'),
            'to_branch': event.get('to_branch'),
            'timestamp': event.get('timestamp')
        } for event in events], indent=2)
    except Exception as e:
        return json.dumps({'error': str(e)}, indent=2), 500