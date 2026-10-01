from fastapi import FastAPI

app = FastAPI(title='code-revup')


@app.get('/health')
def health() -> dict[str, str]:
	return {'status': 'ok'}
