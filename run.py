import os

import uvicorn

if __name__ == "__main__":
    # 既定は自分のPCの中だけ(127.0.0.1)。チームで共有するときは KIMITSU_HOST=0.0.0.0 で
    # 同じ社内LANの他のPCからも届くようにする(第9章)。インターネットに公開する設定ではない。
    host = os.environ.get("KIMITSU_HOST", "127.0.0.1")
    port = int(os.environ.get("KIMITSU_PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port)
