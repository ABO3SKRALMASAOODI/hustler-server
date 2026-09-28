from flask import Flask
from routes import admin


def test_registration_chart_keeps_all_video_history_and_utc_days(monkeypatch):
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql):queries.append(sql)
        def fetchall(self):return [{'day':'2026-08-20','count':66},{'day':'2026-09-28','count':1}]
    class Conn:
        def cursor(self):return Cursor()
        def close(self):closed.append(True)
    queries=[];closed=[]
    monkeypatch.setattr(admin,'get_db',lambda:Conn())
    with Flask(__name__).test_request_context('/admin/charts/registrations'):
        response,status=admin.chart_registrations.__wrapped__()
    body=response.get_json()
    assert status==200 and body['data'][0]['count']==66
    assert body['timezone']=='UTC' and body['scope_start']==admin.METRICS_EPOCH
    assert queries[0]=="SET LOCAL TIME ZONE 'UTC'"
    assert "INTERVAL '30 days'" not in queries[1]
    assert f"generate_series(DATE '{admin.METRICS_EPOCH}', CURRENT_DATE" in queries[1]
    assert admin.ADMIN_EMAIL in queries[1] and 'is_verified = 1' in queries[1]
    assert closed==[True]
