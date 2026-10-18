from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests
from flask import Flask

import email_delivery as delivery
from routes.verify_email import send_code_to_email


@pytest.fixture(autouse=True)
def context(monkeypatch):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'delivery-tests-only-0000000000000000'
    monkeypatch.setenv('BREVO_API_KEY', 'test-only')
    delivery._cache.clear()
    with app.app_context():
        yield


def test_icloud_74_minute_delay_gets_ten_minutes_after_delivery(monkeypatch):
    created = datetime.now(timezone.utc) - timedelta(minutes=75)
    arrived = created + timedelta(minutes=74)
    monkeypatch.setattr(delivery, 'provider_delivery', Mock(return_value={'state': 'delivered', 'delivered_at': arrived}))
    window = delivery.verification_window('challenge', 'test@icloud.com', created, created + timedelta(minutes=10), arrived + timedelta(minutes=1))
    assert window['valid'] and window['expires_in'] == 9 * 60
    assert window['valid_until'] == arrived + timedelta(minutes=10)
    assert not delivery.verification_window('challenge', 'test@icloud.com', created, created + timedelta(minutes=10), arrived + timedelta(minutes=10))['valid']


@pytest.mark.parametrize('state', ['pending', 'unknown'])
def test_reporting_delay_does_not_burn_code_or_allow_unverified_extension(monkeypatch, state):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(delivery, 'provider_delivery', Mock(return_value={'state': state}))
    assert delivery.verification_window('challenge', 'x@example.com', now-timedelta(minutes=1), now+timedelta(minutes=9), now)['valid']
    window = delivery.verification_window('challenge', 'x@example.com', now-timedelta(minutes=15), now-timedelta(minutes=5), now)
    assert not window['valid'] and window['retryable']


def test_hard_bound_and_late_receipts_never_extend_forever(monkeypatch):
    created = datetime.now(timezone.utc) - timedelta(hours=3)
    lookup = Mock(return_value={'state': 'delivered', 'delivered_at': created+timedelta(minutes=121)})
    monkeypatch.setattr(delivery, 'provider_delivery', lookup)
    assert not delivery.verification_window('challenge', 'x@example.com', created, created+timedelta(minutes=10))['valid']
    lookup.assert_not_called()
    assert not delivery.verification_window('challenge', 'x@example.com', created, created+timedelta(minutes=10), created+timedelta(minutes=122))['valid']


def test_only_exact_tag_and_recipient_first_delivery_count(monkeypatch):
    now = datetime.now(timezone.utc)
    created = now - timedelta(minutes=20)
    tag = delivery.delivery_tag('secret-challenge')
    def event(kind, minutes, **extra):
        return {'event': kind, 'date': (created+timedelta(minutes=minutes)).isoformat(), 'tag': 'valmera-verification,'+tag, 'email': 'x@example.com', **extra}
    get = Mock(return_value=Mock(status_code=200, json=Mock(return_value={'events': [
        event('delivered', 19), event('delivered', 5), event('proxyOpen', 20),
        event('delivered', 1, email='other@example.com'), event('delivered', 2, tag='other'),
        event('delivered', -1), event('delivered', 25),
    ]})))
    monkeypatch.setattr(delivery.requests, 'get', get)
    result = delivery.provider_delivery(tag, 'x@example.com', created)
    assert result == {'state': 'delivered', 'delivered_at': created+timedelta(minutes=5)}
    assert delivery.provider_delivery(tag, 'x@example.com', created) == result
    get.assert_called_once()
    assert get.call_args.kwargs['params']['tags'] == tag
    # A repeated delivery or an Apple privacy open cannot refresh an expired code.
    assert not delivery.verification_window('secret-challenge', 'x@example.com', created, created+timedelta(minutes=10), now)['valid']


@pytest.mark.parametrize('failure', [requests.Timeout(), ValueError('invalid JSON')])
def test_provider_failures_are_retryable(monkeypatch, failure):
    monkeypatch.setattr(delivery.requests, 'get', Mock(side_effect=failure))
    assert delivery.provider_delivery('tag', 'x@example.com', datetime.now(timezone.utc)) == {'state': 'unknown'}


@pytest.mark.parametrize('event,state', [('hardBounces','undeliverable'),('blocked','undeliverable'),('proxyOpen','pending'),('requests','pending')])
def test_delivery_states_never_treat_open_as_delivery(monkeypatch, event, state):
    now = datetime.now(timezone.utc)
    response = Mock(status_code=200, json=Mock(return_value={'events': [{'event': event, 'date': now.isoformat(), 'tag': 'tag', 'email': 'x@example.com'}]}))
    monkeypatch.setattr(delivery.requests, 'get', Mock(return_value=response))
    assert delivery.provider_delivery('tag', 'x@example.com', now-timedelta(seconds=2)) == {'state': state}


def test_verification_email_is_lightweight_and_tag_contains_no_login_secret(monkeypatch):
    send = Mock(return_value=True)
    monkeypatch.setattr('brevo_delivery.send_email', send)
    assert send_code_to_email('x@icloud.com', '123456', challenge='d2_'+'a'*40, delivery_aware=True)
    payload = send.call_args.args[0]
    assert '10 minutes of delivery' in payload['textContent']
    assert '<img' not in payload['htmlContent'] and '<a ' not in payload['htmlContent']
    assert payload['tags'][0] == 'valmera-verification'
    assert all('123456' not in tag and 'd2_' not in tag and '@' not in tag for tag in payload['tags'])
    assert send.call_args.kwargs['category'] == 'critical'
