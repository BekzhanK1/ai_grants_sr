ai:
	cd ai-admin && . .venv/bin/activate && uvicorn app.main:app --reload --port 8555

backend:
	cd myspace-backend && . .venv/bin/activate && DJANGO_CONFIGURATION=Dev python manage.py runserver

frontend:
	cd myspace-frontend && npm run dev