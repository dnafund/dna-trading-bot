"""
Database Manager for My-Life-OS V3

Handles all database operations with SQLAlchemy ORM.
Supports SQLite (local) and PostgreSQL (production).

Author: datthieu + Claude
Date: 2026-01-27
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    Boolean, JSON, Text, Index, func
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from contextlib import contextmanager

Base = declarative_base()


# ============================================================================
# Database Models
# ============================================================================

class TradingSignal(Base):
    """Store trading signals (BUY/SELL/NEUTRAL)"""
    __tablename__ = 'trading_signals'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    symbol = Column(String(20), index=True)  # BTC/USDT
    signal = Column(String(10))  # BUY/SELL/NEUTRAL
    rsi = Column(Float)
    ema = Column(Float)
    price = Column(Float)
    volume = Column(Float)
    meta_data = Column(JSON)  # Additional data (renamed from metadata)

    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'symbol': self.symbol,
            'signal': self.signal,
            'rsi': self.rsi,
            'ema': self.ema,
            'price': self.price,
            'volume': self.volume,
            'meta_data': self.meta_data
        }


class OperationLog(Base):
    """Log all operations for audit and analysis"""
    __tablename__ = 'operations_log'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    operation_name = Column(String(100), index=True)
    risk_score = Column(Integer)
    status = Column(String(20))  # success/failed/blocked
    execution_time_ms = Column(Integer)
    fast_track = Column(Boolean, default=False, index=True)
    auto_approved = Column(Boolean, default=False)
    linear_ticket_id = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    meta_data = Column(JSON)  # Renamed from metadata

    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'operation_name': self.operation_name,
            'risk_score': self.risk_score,
            'status': self.status,
            'execution_time_ms': self.execution_time_ms,
            'fast_track': self.fast_track,
            'auto_approved': self.auto_approved,
            'linear_ticket_id': self.linear_ticket_id
        }


class KnowledgeLesson(Base):
    """Store self-improvement lessons"""
    __tablename__ = 'knowledge_lessons'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    category = Column(String(50), index=True)  # trading/development/etc
    problem = Column(Text)
    root_cause = Column(Text)
    solution = Column(Text)
    confidence = Column(Integer)  # 0-100
    status = Column(String(20), default='pending')  # pending/approved/rejected
    linear_ticket_id = Column(String(50), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(50), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'category': self.category,
            'problem': self.problem,
            'root_cause': self.root_cause,
            'solution': self.solution,
            'confidence': self.confidence,
            'status': self.status,
            'linear_ticket_id': self.linear_ticket_id
        }


class DailyMetrics(Base):
    """Aggregate daily metrics for monitoring"""
    __tablename__ = 'daily_metrics'

    date = Column(DateTime, primary_key=True)
    operations_total = Column(Integer, default=0)
    operations_fast_track = Column(Integer, default=0)
    operations_blocked = Column(Integer, default=0)
    operations_auto_approved = Column(Integer, default=0)
    tickets_created = Column(Integer, default=0)
    avg_risk_score = Column(Float)
    avg_execution_time_ms = Column(Float)
    system_health = Column(JSON)  # Custom health metrics

    def to_dict(self):
        return {
            'date': self.date.date().isoformat(),
            'operations_total': self.operations_total,
            'operations_fast_track': self.operations_fast_track,
            'operations_blocked': self.operations_blocked,
            'operations_auto_approved': self.operations_auto_approved,
            'tickets_created': self.tickets_created,
            'avg_risk_score': self.avg_risk_score,
            'avg_execution_time_ms': self.avg_execution_time_ms
        }


class MultiTimeframeData(Base):
    """Store multi-timeframe trading analysis"""
    __tablename__ = 'multi_timeframe_data'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    symbol = Column(String(20), index=True)

    # H4 data (strategic)
    h4_rsi = Column(Float)
    h4_ema = Column(Float)
    h4_signal = Column(String(10))  # BUY/SELL/NEUTRAL
    h4_confidence = Column(Float)

    # M15 data (tactical)
    m15_rsi = Column(Float, nullable=True)
    m15_momentum = Column(Float, nullable=True)
    m15_entry_ready = Column(Boolean, default=False)

    # Metadata
    is_active = Column(Boolean, default=False, index=True)
    m15_polling_enabled = Column(Boolean, default=False)
    last_updated = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'symbol': self.symbol,
            'h4_signal': self.h4_signal,
            'h4_rsi': self.h4_rsi,
            'm15_entry_ready': self.m15_entry_ready,
            'is_active': self.is_active
        }


# ============================================================================
# Database Manager
# ============================================================================

class DatabaseManager:
    """
    Manage all database operations for My-Life-OS V3

    Supports:
    - SQLite (local development)
    - PostgreSQL (production)
    """

    def __init__(self, db_url: str = 'sqlite:///database/my_life_os.db'):
        """
        Initialize database manager

        Args:
            db_url: Database connection string
                SQLite: sqlite:///path/to/db.db
                PostgreSQL: postgresql://user:pass@localhost/dbname
        """
        self.db_url = db_url
        self.engine = create_engine(db_url, echo=False)

        # Create tables if not exist
        Base.metadata.create_all(self.engine)

        # Create session factory
        self.SessionLocal = sessionmaker(bind=self.engine)

        print(f"✅ Database initialized: {db_url}")

    @contextmanager
    def get_session(self) -> Session:
        """Context manager for database sessions"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    # ========================================================================
    # Trading Signals
    # ========================================================================

    def store_trading_signal(
        self,
        symbol: str,
        signal: str,
        rsi: float,
        ema: float,
        price: float,
        volume: float,
        metadata: Optional[Dict] = None
    ) -> int:
        """Store a trading signal"""
        with self.get_session() as session:
            signal_obj = TradingSignal(
                symbol=symbol,
                signal=signal,
                rsi=rsi,
                ema=ema,
                price=price,
                volume=volume,
                meta_data=metadata or {}
            )
            session.add(signal_obj)
            session.flush()
            return signal_obj.id

    def get_recent_signals(
        self,
        symbol: Optional[str] = None,
        hours: int = 24
    ) -> List[Dict]:
        """Get recent trading signals"""
        with self.get_session() as session:
            query = session.query(TradingSignal).filter(
                TradingSignal.timestamp >= datetime.utcnow() - timedelta(hours=hours)
            )

            if symbol:
                query = query.filter(TradingSignal.symbol == symbol)

            signals = query.order_by(TradingSignal.timestamp.desc()).all()
            return [s.to_dict() for s in signals]

    def count_signals(self) -> int:
        """Count total signals in database"""
        with self.get_session() as session:
            return session.query(TradingSignal).count()

    # ========================================================================
    # Operations Log
    # ========================================================================

    def log_operation(
        self,
        operation_name: str,
        risk_score: int,
        status: str,
        execution_time_ms: Optional[int] = None,
        fast_track: bool = False,
        auto_approved: bool = False,
        linear_ticket_id: Optional[str] = None,
        error_message: Optional[str] = None,
        meta_data: Optional[Dict] = None
    ) -> int:
        """Log an operation execution"""
        with self.get_session() as session:
            log = OperationLog(
                operation_name=operation_name,
                risk_score=risk_score,
                status=status,
                execution_time_ms=execution_time_ms or 0,
                fast_track=fast_track,
                auto_approved=auto_approved,
                linear_ticket_id=linear_ticket_id,
                error_message=error_message,
                meta_data=meta_data or {}
            )
            session.add(log)
            session.flush()
            return log.id

    def get_operation_history(
        self,
        operation_name: str,
        days: int = 7
    ) -> Dict[str, Any]:
        """Get operation history for auto-approve check"""
        with self.get_session() as session:
            since = datetime.utcnow() - timedelta(days=days)

            total = session.query(OperationLog).filter(
                OperationLog.operation_name == operation_name,
                OperationLog.timestamp >= since
            ).count()

            failures = session.query(OperationLog).filter(
                OperationLog.operation_name == operation_name,
                OperationLog.timestamp >= since,
                OperationLog.status == 'failed'
            ).count()

            return {
                'operation_name': operation_name,
                'days': days,
                'total': total,
                'failures': failures,
                'success_rate': (total - failures) / total if total > 0 else 0
            }

    def count_operations(self) -> int:
        """Count total operations logged"""
        with self.get_session() as session:
            return session.query(OperationLog).count()

    def get_avg_execution_time(self) -> float:
        """Get average execution time across all operations"""
        with self.get_session() as session:
            result = session.query(
                func.avg(OperationLog.execution_time_ms)
            ).scalar()
            return result or 0.0

    def get_fast_track_percentage(self) -> float:
        """Get percentage of operations using fast track"""
        with self.get_session() as session:
            total = session.query(OperationLog).count()
            if total == 0:
                return 0.0

            fast_track_count = session.query(OperationLog).filter(
                OperationLog.fast_track == True
            ).count()

            return (fast_track_count / total) * 100

    def get_auto_approve_rate(self) -> float:
        """Get auto-approve rate"""
        with self.get_session() as session:
            total = session.query(OperationLog).count()
            if total == 0:
                return 0.0

            auto_approved = session.query(OperationLog).filter(
                OperationLog.auto_approved == True
            ).count()

            return (auto_approved / total) * 100

    # ========================================================================
    # Knowledge Lessons
    # ========================================================================

    def store_lesson(
        self,
        category: str,
        problem: str,
        root_cause: str,
        solution: str,
        confidence: int,
        linear_ticket_id: Optional[str] = None
    ) -> int:
        """Store a knowledge lesson"""
        with self.get_session() as session:
            lesson = KnowledgeLesson(
                category=category,
                problem=problem,
                root_cause=root_cause,
                solution=solution,
                confidence=confidence,
                linear_ticket_id=linear_ticket_id
            )
            session.add(lesson)
            session.flush()
            return lesson.id

    def approve_lesson(self, lesson_id: int, approved_by: str) -> bool:
        """Approve a lesson (after orchestrator review)"""
        with self.get_session() as session:
            lesson = session.query(KnowledgeLesson).filter_by(id=lesson_id).first()
            if lesson:
                lesson.status = 'approved'
                lesson.approved_at = datetime.utcnow()
                lesson.approved_by = approved_by
                return True
            return False

    def count_lessons(self) -> int:
        """Count total lessons"""
        with self.get_session() as session:
            return session.query(KnowledgeLesson).count()

    # ========================================================================
    # Daily Metrics
    # ========================================================================

    def update_daily_metrics(self):
        """Update today's metrics (run daily)"""
        with self.get_session() as session:
            today = datetime.utcnow().date()
            today_start = datetime.combine(today, datetime.min.time())

            # Calculate metrics
            total = session.query(OperationLog).filter(
                OperationLog.timestamp >= today_start
            ).count()

            fast_track = session.query(OperationLog).filter(
                OperationLog.timestamp >= today_start,
                OperationLog.fast_track == True
            ).count()

            blocked = session.query(OperationLog).filter(
                OperationLog.timestamp >= today_start,
                OperationLog.status == 'blocked'
            ).count()

            auto_approved = session.query(OperationLog).filter(
                OperationLog.timestamp >= today_start,
                OperationLog.auto_approved == True
            ).count()

            # Upsert metrics
            metrics = session.query(DailyMetrics).filter_by(date=today_start).first()
            if not metrics:
                metrics = DailyMetrics(date=today_start)
                session.add(metrics)

            metrics.operations_total = total
            metrics.operations_fast_track = fast_track
            metrics.operations_blocked = blocked
            metrics.operations_auto_approved = auto_approved

    def get_daily_metrics(self, days: int = 7) -> List[Dict]:
        """Get daily metrics for last N days"""
        with self.get_session() as session:
            since = datetime.utcnow() - timedelta(days=days)
            metrics = session.query(DailyMetrics).filter(
                DailyMetrics.date >= since
            ).order_by(DailyMetrics.date).all()

            return [m.to_dict() for m in metrics]


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == '__main__':
    print("=" * 80)
    print("Database Manager - Example Usage")
    print("=" * 80)
    print()

    # Initialize
    db = DatabaseManager('sqlite:///database/my_life_os_test.db')

    # Store trading signal
    signal_id = db.store_trading_signal(
        symbol='BTC/USDT',
        signal='BUY',
        rsi=28.5,
        ema=104891.0,
        price=104950.0,
        volume=1234567.89
    )
    print(f"✅ Stored trading signal: ID {signal_id}")

    # Log operation
    log_id = db.log_operation(
        operation_name='scan_market',
        risk_score=5,
        status='success',
        execution_time_ms=250,
        fast_track=True
    )
    print(f"✅ Logged operation: ID {log_id}")

    # Get stats
    print()
    print("📊 Database Stats:")
    print(f"   Total signals: {db.count_signals()}")
    print(f"   Total operations: {db.count_operations()}")
    print(f"   Avg execution time: {db.get_avg_execution_time():.2f}ms")
    print(f"   Fast track %: {db.get_fast_track_percentage():.1f}%")

    print()
    print("=" * 80)
    print("✅ Database Manager working correctly")
    print("=" * 80)
