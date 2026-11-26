import os

import modal


app = modal.App("simple-pdf-editor")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements.txt")
    .add_local_file("main.py", remote_path="/root/main.py")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("simple-pdf-editor-secrets")],
    max_containers=1,
    scaledown_window=300,
)
@modal.asgi_app()
def serve_api():
    required_settings = ("PDF_EDITOR_SHARED_PASSWORD", "PDF_EDITOR_ALLOWED_ORIGINS")
    missing = [key for key in required_settings if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"Missing Modal secret settings: {', '.join(missing)}")
    os.environ["PDF_EDITOR_REQUIRE_AUTH"] = "1"
    os.environ["PDF_EDITOR_SERVE_STATIC"] = "0"

    from main import app as fastapi_app

    return fastapi_app
