import { Component, type ErrorInfo, type ReactNode } from "react";

export default class ErrorBoundary extends Component<{ children: ReactNode }, { message: string | null }> {
  state = { message: null };

  static getDerivedStateFromError(error: Error) {
    return { message: error.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("EGX Smart Screener render error", error, info);
  }

  render() {
    if (this.state.message) {
      return (
        <div className="grid min-h-screen place-items-center bg-terminal-bg p-6 text-slate-100">
          <div className="max-w-2xl rounded-lg border border-red-500/30 bg-terminal-card p-5">
            <h1 className="text-lg font-bold text-red-300">App render error</h1>
            <p className="mt-3 text-sm leading-6 text-slate-300">{this.state.message}</p>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
