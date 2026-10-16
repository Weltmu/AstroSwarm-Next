import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import './tokens.css';
import './styles.css';
/* 统一控件库（开关 / 胶囊按钮 / 折叠 / 数值输入）2026-10-12。
   单独一个文件、最后引入：styles.css 里有两段 gen_pins.py 生成的「自动钉死/自动求解」
   规则不能手改，控件样式另起一份就不会被那两段覆盖，也不会覆盖别人的既有规则。 */
import './components.css';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, color: '#dfe8ff', fontFamily: 'Inter, sans-serif' }}>
          <h2>页面出错了</h2>
          <pre style={{ whiteSpace: 'pre-wrap', color: '#f59e0b' }}>
            {String(this.state.error && this.state.error.message)}
          </pre>
          <button
            style={{
              marginTop: 16,
              padding: '9px 18px',
              borderRadius: 10,
              border: 'none',
              background: '#56d8ff',
              color: '#06121f',
              fontWeight: 700,
              cursor: 'pointer',
            }}
            onClick={() => window.location.reload()}
          >
            重新加载
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
);
