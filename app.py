"""保留旧启动命令：streamlit run app.py。主版本入口为 web_app.py。"""
from legacy.streamlit.app import main


if __name__ == "__main__":
    main()
