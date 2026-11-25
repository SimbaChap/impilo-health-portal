"""Sign-in for the National Health Portal.

Accounts live in api/auth-data/users.json (passwords stored only as salted PBKDF2-SHA256 hashes).
A successful sign-in returns a signed session token (HMAC-SHA256, 12-hour lifetime) that the pages send as
"Authorization: Bearer <token>". Every /api/* request needs one, except the few listed in PUBLIC.

  GET  /api/auth/status     {setupRequired}: true until the first administrator exists
  POST /api/auth/setup      create the first administrator (only while there are no users)
  POST /api/auth/login      {username, password} -> {token, user, expires}
  GET  /api/auth/me         the signed-in user
  POST /api/auth/password   change your own password
  GET|POST|PUT|DELETE /api/auth/users  administrators manage accounts
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

AUTH_DIR = Path(__file__).with_name('auth-data')
AUTH_DIR.mkdir(exist_ok=True)
USERS_FILE = AUTH_DIR / 'users.json'
SECRET_FILE = AUTH_DIR / 'secret.key'
SESSION_SECONDS = 12 * 3600
PBKDF2_ROUNDS = 310_000
PUBLIC = {'/api/health', '/api/auth/status', '/api/auth/setup', '/api/auth/login'}
ROLES = ('admin', 'viewer')

router = APIRouter(prefix='/api/auth', tags=['Sign-in'])
_lock = threading.Lock()


def _secret():
    if not SECRET_FILE.exists():
        SECRET_FILE.write_bytes(secrets.token_bytes(32))
        try:
            os.chmod(SECRET_FILE, 0o600)
        except OSError:
            pass
    return SECRET_FILE.read_bytes()


SECRET = _secret()


# ------------------------------------------------------------------ users
def _users():
    try:
        return json.loads(USERS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []


def _save(users):
    tmp = USERS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(users, indent=2), encoding='utf-8')
    tmp.replace(USERS_FILE)


def _hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ROUNDS)
    return base64.b64encode(salt).decode(), base64.b64encode(digest).decode()


def _check(password, user):
    salt = base64.b64decode(user['salt'])
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, user.get('rounds', PBKDF2_ROUNDS))
    return hmac.compare_digest(base64.b64encode(digest).decode(), user['hash'])


def _public(u):
    import access
    return {k: u.get(k) for k in ('username', 'name', 'role', 'active', 'created', 'lastLogin', 'email', 'note')} | access.permissions(u)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _valid_password(p):
    if len(p) < 10:
        raise HTTPException(400, 'Use at least 10 characters for the password.')
    if p.lower() == p or p.upper() == p or not re.search(r'\d', p):
        raise HTTPException(400, 'Use a mix of upper- and lower-case letters and at least one number.')


# ------------------------------------------------------------------ tokens
def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def issue_token(user):
    payload = {'u': user['username'], 'r': user['role'], 'exp': int(time.time()) + SESSION_SECONDS, 'v': user.get('tokenVersion', 0)}
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    sig = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f'{body}.{sig}', payload['exp']


def read_token(token):
    try:
        body, sig = token.split('.')
        if not hmac.compare_digest(_b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest()), sig):
            return None
        p = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if p.get('exp', 0) < time.time():
        return None
    user = next((u for u in _users() if u['username'] == p.get('u')), None)
    # Disabled accounts and changed passwords end existing sessions.
    if not user or not user.get('active', True) or user.get('tokenVersion', 0) != p.get('v', 0):
        return None
    return user


def current_user(request: Request):
    user = getattr(request.state, 'user', None)
    if not user:
        raise HTTPException(401, 'Please sign in.')
    return user


def require_admin(request: Request):
    user = current_user(request)
    if user['role'] != 'admin':
        raise HTTPException(403, 'Only administrators can do this.')
    return user


async def auth_middleware(request: Request, call_next):
    """Every /api/* call needs a valid session, except sign-in itself and the health check."""
    path = request.url.path
    if request.method == 'OPTIONS' or not path.startswith('/api/') or path in PUBLIC:
        return await call_next(request)
    header = request.headers.get('authorization', '')
    user = read_token(header[7:]) if header.lower().startswith('bearer ') else None
    if not user:
        return JSONResponse({'detail': 'Please sign in.'}, status_code=401, headers={'Cache-Control': 'no-store'})
    request.state.user = user
    # Sections, area, downloads and patient-level data (see access.py). AI configuration and user
    # management stay with administrators.
    import access
    refused = access.check(request, user)
    if refused is not None:
        return refused
    return await call_next(request)


# ------------------------------------------------------------------ brute-force protection
_fails = {}


def _throttle(key):
    times = [t for t in _fails.get(key, []) if time.time() - t < 900]
    _fails[key] = times
    if len(times) >= 5:
        wait = int(900 - (time.time() - times[0])) // 60 + 1
        raise HTTPException(429, f'Too many failed attempts. Try again in {wait} minute{"s" if wait != 1 else ""}.')


# ------------------------------------------------------------------ endpoints
class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r'^[A-Za-z0-9._@-]+$')
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(max_length=200)


class LoginRequest(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(max_length=200)


class PasswordRequest(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class AccessRequest(BaseModel):
    level: str = Field('national', pattern='^(national|province|district|facility)$')
    areas: list[str] = Field([], max_length=500)


class DownloadsRequest(BaseModel):
    reports: bool = False
    registers: bool = False
    raw: bool = False


class UserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r'^[A-Za-z0-9._@-]+$')
    name: str = Field(min_length=1, max_length=80)
    role: str = Field('viewer', pattern='^(admin|viewer)$')
    password: str | None = Field(None, max_length=200)
    active: bool = True
    # Leave these out to keep what the account already has.
    access: AccessRequest | None = None
    modules: list[str] | None = None
    patientData: bool | None = None
    downloads: DownloadsRequest | None = None
    email: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=300)


@router.get('/status')
def auth_status():
    return {'setupRequired': not _users()}


@router.post('/setup')
def setup(req: SetupRequest):
    with _lock:
        if _users():
            raise HTTPException(409, 'The portal already has accounts. Sign in instead.')
        _valid_password(req.password)
        salt, digest = _hash(req.password)
        user = {'username': req.username.lower(), 'name': req.name.strip(), 'role': 'admin', 'active': True, 'salt': salt, 'hash': digest,
                'rounds': PBKDF2_ROUNDS, 'created': _now(), 'lastLogin': _now(), 'tokenVersion': 0}
        _save([user])
    token, exp = issue_token(user)
    return {'token': token, 'expires': exp, 'user': _public(user)}


@router.post('/login')
def login(req: LoginRequest, request: Request):
    key = req.username.lower().strip()
    _throttle(key)
    _throttle('ip:' + (request.client.host if request.client else ''))
    with _lock:
        users = _users()
        user = next((u for u in users if u['username'] == key), None)
        ok = bool(user) and user.get('active', True) and _check(req.password, user)
        if not ok:
            if not user:
                _hash(req.password)  # same work either way, so timing doesn't reveal valid usernames
            _fails.setdefault(key, []).append(time.time())
            _fails.setdefault('ip:' + (request.client.host if request.client else ''), []).append(time.time())
            raise HTTPException(401, 'Wrong username or password.' if not user or user.get('active', True) else 'This account is disabled.')
        _fails.pop(key, None)
        user['lastLogin'] = _now()
        _save(users)
    token, exp = issue_token(user)
    return {'token': token, 'expires': exp, 'user': _public(user)}


@router.get('/me')
def me(request: Request):
    return _public(current_user(request))


@router.post('/password')
def change_password(req: PasswordRequest, request: Request):
    user = current_user(request)
    with _lock:
        users = _users()
        u = next(x for x in users if x['username'] == user['username'])
        if not _check(req.current, u):
            raise HTTPException(400, 'Your current password is not correct.')
        _valid_password(req.new)
        u['salt'], u['hash'] = _hash(req.new)
        u['rounds'] = PBKDF2_ROUNDS
        u['tokenVersion'] = u.get('tokenVersion', 0) + 1  # signs out other sessions
        _save(users)
    token, exp = issue_token(u)
    return {'token': token, 'expires': exp, 'user': _public(u)}


@router.get('/users')
def list_users(request: Request):
    require_admin(request)
    return {'users': [_public(u) for u in _users()]}


@router.put('/users')
def save_user(req: UserRequest, request: Request):
    admin = require_admin(request)
    with _lock:
        users = _users()
        name = req.username.lower()
        u = next((x for x in users if x['username'] == name), None)
        if u is None:
            if not req.password:
                raise HTTPException(400, 'Set a password for the new account.')
            u = {'username': name, 'created': _now(), 'lastLogin': None, 'tokenVersion': 0}
            users.append(u)
        if name == admin['username'] and (req.role != 'admin' or not req.active):
            raise HTTPException(400, "You can't remove your own administrator access.")
        import access
        u.update(name=req.name.strip(), role=req.role, active=req.active)
        if req.access is not None:
            try:
                access.validate(req.access.level, req.access.areas, req.modules or [], None)
            except ValueError as e:
                raise HTTPException(400, str(e))
            u['access'] = {'level': req.access.level, 'areas': req.access.areas if req.access.level != 'national' else []}
        if req.modules is not None:
            try:
                access.validate('national', [], req.modules, None)
            except ValueError as e:
                raise HTTPException(400, str(e))
            u['modules'] = [m for m in req.modules if m not in access.ADMIN_ONLY]
        if req.patientData is not None: u['patientData'] = req.patientData
        if req.downloads is not None: u['downloads'] = req.downloads.model_dump()
        if req.email is not None: u['email'] = req.email.strip()
        if req.note is not None: u['note'] = req.note.strip()
        if req.password:
            _valid_password(req.password)
            u['salt'], u['hash'] = _hash(req.password)
            u['rounds'] = PBKDF2_ROUNDS
            u['tokenVersion'] = u.get('tokenVersion', 0) + 1
        if not req.active:
            u['tokenVersion'] = u.get('tokenVersion', 0) + 1
        _save(users)
    return _public(u)


@router.delete('/users/{username}')
def delete_user(username: str, request: Request):
    admin = require_admin(request)
    if username.lower() == admin['username']:
        raise HTTPException(400, "You can't delete your own account.")
    with _lock:
        users = _users()
        if not any(u['username'] == username.lower() for u in users):
            raise HTTPException(404, 'No such account.')
        _save([u for u in users if u['username'] != username.lower()])
    return {'deleted': username.lower()}
