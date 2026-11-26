"""Firebase-owned scheduled publisher. No API keys or repository credentials."""
import google.auth
from google.auth.transport.requests import Request
from firebase_functions import scheduler_fn,options
from scripts.publish_fixtures import run_update


@scheduler_fn.on_schedule(
    schedule='10 */3 * * *',timezone='Europe/Istanbul',region='europe-west3',
    memory=options.MemoryOption.MB_512,cpu='gcf_gen1',timeout_sec=240,
    min_instances=0,max_instances=1,concurrency=1,retry_count=0,
    service_account='asistan-sports-runtime@asistan-8a2cf.iam.gserviceaccount.com',
)
def refresh_sports(event:scheduler_fn.ScheduledEvent)->None:
    credentials,project=google.auth.default(scopes=['https://www.googleapis.com/auth/datastore'])
    if project!='asistan-8a2cf':raise RuntimeError('Unexpected runtime project')
    credentials.refresh(Request())
    result=run_update(project,credentials.token)
    print(f'Firebase sports publication complete: {len(result["events"])} events')
