import os
import sys

import pytest

os.environ.setdefault("ARXIVLETTER_SCHEDULER", "0")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh isolated database + data dir per test (every module that bound `settings` sees the temp one)."""
    from arxivletter import config
    from arxivletter.storage import db

    monkeypatch.setenv("ARXIVLETTER_DATA_DIR", str(tmp_path))
    s = config.Settings()
    monkeypatch.setattr(config, "settings", s)
    for mod in ("arxivletter.api.app", "arxivletter.orchestration.pipeline", "arxivletter.arxiv.source",
                "arxivletter.orchestration.jobs", "arxivletter.orchestration.llm", "arxivletter.orchestration.models",
                "arxivletter.analysis.relevance", "arxivletter.analysis.text"):
        __import__(mod)
    for name, m in list(sys.modules.items()):
        if name.startswith("arxivletter") and getattr(m, "settings", None) is not None and m is not config:
            monkeypatch.setattr(m, "settings", s)
    db.init(s.db_path)
    yield s
    db._local.__dict__.clear()


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    """Tests never reach a real model or arXiv: only adapters a test installs count as available."""
    from arxivletter.orchestration import llm, models
    monkeypatch.setattr(models, "_avail_cache", {})
    monkeypatch.setattr(models, "available", lambda mid: mid in llm._adapters or (mid == "local" and "local" in llm._adapters))
    yield
    llm._adapters.clear()


class FakeAdapter:
    """Returns canned responses (callable or list) and records requests."""

    def __init__(self, name="fake-local", responses=None, model_class="local_text"):
        self.name = name
        self.model_class = model_class
        self.supports_vision = False
        self.decoding = ""
        self.responses = responses
        self.requests = []

    def health(self):
        return True

    def chat(self, req):
        from arxivletter.model_adapters.base import ChatResult
        self.requests.append(req)
        r = self.responses(req) if callable(self.responses) else self.responses.pop(0)
        return ChatResult(r, self.name, 100, 50, 0.01, "stop")


@pytest.fixture
def fake_local(monkeypatch):
    from arxivletter.orchestration import llm
    import threading

    def install(responses, mid="local", name="fake-local"):
        ad = FakeAdapter(name, responses)
        llm._adapters[mid] = ad
        llm._sems[mid] = threading.Semaphore(4)
        return ad
    return install


SAMPLE_TEX = r"""
\documentclass[aps,prd,twocolumn]{revtex4-2}
\usepackage{amsmath}
\newcommand{\mchi}{m_\chi}
\newcommand{\beq}{\begin{equation}}
\newcommand{\eeq}{\end{equation}}
\newcommand\bea{\begin{eqnarray}}
\newcommand\eea{\end{eqnarray}}
\DeclareMathOperator{\Tr}{Tr}
\begin{document}
\title{Freeze-in of Dark Matter in Strong Fields\thanks{funded}}
\author{A. Author}
\email{a@b.c}
\affiliation{Somewhere University}
\begin{abstract}
We compute the relic abundance of $\chi$ produced via the Schwinger effect.
\end{abstract}
\maketitle

\section{Introduction}\label{sec:intro}
Dark matter (DM) with mass $\mchi$ may be produced non-perturbatively~\cite{Schwinger:1951nm,Gies:2005}.
We show this in Sec.~\ref{sec:model}, see Eq.~\eqref{eq:rate}.\footnote{A footnote with $x^2$.}

This is a second paragraph with \emph{emphasis} and 10\% precision.

\section{Model}\label{sec:model}
The pair-production rate is
\beq
\Gamma = \frac{(eE)^2}{4\pi^3}\, e^{-\pi \mchi^2/(eE)} \label{eq:rate}
\eeq
where $E$ is the field strength.
\bea
a &=& b \nonumber \\
c &=& d \label{eq:cd}
\eea
\begin{subequations}
\begin{align}
x &= 1 \label{eq:x}\\
y &= 2
\end{align}
\end{subequations}
\subsection{Caveats}
We neglect back-reaction of the produced pairs on the field, which we leave for future work.
It remains an open question whether the rate survives in inhomogeneous fields.
\begin{figure}[t]
\includegraphics[width=0.45\textwidth]{fig1.pdf}
\caption{The rate as a function of $E$.}\label{fig:rate}
\end{figure}
\appendix
\section{Details}
\begin{equation}
\Tr\, F^2 = 0 \label{eq:app}
\end{equation}
See Fig.~\ref{fig:rate} and Eq.~(\ref{eq:app}).
\begin{thebibliography}{9}
\bibitem{Schwinger:1951nm} J.~Schwinger, Phys. Rev. \textbf{82}, 664 (1951).
\bibitem{Gies:2005} H.~Gies and K.~Klingm\"uller, arXiv:hep-ph/0505099.
\end{thebibliography}
\end{document}
"""


@pytest.fixture
def sample_doc():
    from arxivletter.parsing import latex
    return latex.parse({"main.tex": SAMPLE_TEX})
