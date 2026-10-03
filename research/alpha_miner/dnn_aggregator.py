"""Deep Neural Network Aggregator for Alpha Factors.

Implements DNN-based aggregation of multiple alpha factors into a single
composite signal, as described in the paper.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Optional PyTorch import
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    logger.warning("PyTorch not available. DNN aggregation will use fallback.")


# ---------------------------------------------------------------------------
# DNN Model
# ---------------------------------------------------------------------------

class AlphaAggregatorNet(nn.Module if HAS_TORCH else object):
    """Neural Network for alpha factor aggregation."""
    
    def __init__(self, input_dim: int, hidden_dims: List[int] = [128, 64, 32],
                 dropout: float = 0.2):
        """Initialize DNN model.
        
        Args:
            input_dim: Number of input alpha factors
            hidden_dims: Hidden layer dimensions
            dropout: Dropout rate
        """
        if not HAS_TORCH:
            return
        
        super().__init__()
        
        layers = []
        prev_dim = input_dim
        
        for hidden_dim in hidden_dims:
            layers.extend([
                nn.Linear(prev_dim, hidden_dim),
                nn.BatchNorm1d(hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
            ])
            prev_dim = hidden_dim
        
        # Output layer: probability of positive 5-day return
        layers.append(nn.Linear(prev_dim, 1))
        layers.append(nn.Sigmoid())
        
        self.model = nn.Sequential(*layers)
    
    def forward(self, x):
        """Forward pass."""
        return self.model(x)


# ---------------------------------------------------------------------------
# DNN Aggregator
# ---------------------------------------------------------------------------

@dataclass
class AggregationResult:
    """Result of DNN aggregation."""
    predictions: np.ndarray              # Predicted probabilities
    portfolio_weights: np.ndarray        # Long-only portfolio weights
    model_loss: float                    # Final training loss
    feature_importance: Dict[int, float] # Feature importance (gradient-based)
    n_alphas: int                        # Number of input alphas
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "n_alphas": self.n_alphas,
            "mean_prediction": float(np.mean(self.predictions)),
            "std_prediction": float(np.std(self.predictions)),
            "model_loss": round(self.model_loss, 6),
            "n_positive_predictions": int(np.sum(self.predictions > 0.5)),
        }


class DNNAggregator:
    """Deep Neural Network for alpha factor aggregation."""
    
    def __init__(self, input_dim: int, hidden_dims: List[int] = [128, 64, 32],
                 dropout: float = 0.2, learning_rate: float = 0.001,
                 device: str = "cpu"):
        """Initialize DNN aggregator.
        
        Args:
            input_dim: Number of input alpha factors
            hidden_dims: Hidden layer dimensions
            dropout: Dropout rate
            learning_rate: Learning rate
            device: Device for training (cpu/cuda)
        """
        self.input_dim = input_dim
        self.hidden_dims = hidden_dims
        self.dropout = dropout
        self.learning_rate = learning_rate
        self.device = device
        
        self.model = None
        self.scaler = None
        
        if HAS_TORCH:
            self.model = AlphaAggregatorNet(input_dim, hidden_dims, dropout)
            self.model.to(device)
    
    def train(self, alpha_panel: pd.DataFrame,
              labels: pd.Series,
              epochs: int = 100,
              batch_size: int = 32,
              validation_split: float = 0.2,
              verbose: bool = True) -> Dict[str, float]:
        """Train DNN on alpha factors.
        
        Args:
            alpha_panel: DataFrame with alpha values (n_samples, n_alphas)
            labels: Binary labels (1 = positive 5-day return, 0 = negative)
            epochs: Number of training epochs
            batch_size: Batch size
            validation_split: Fraction of data for validation
            verbose: Whether to print training progress
            
        Returns:
            Dictionary with training metrics
        """
        if not HAS_TORCH:
            logger.warning("PyTorch not available. Using fallback aggregation.")
            return self._fallback_train(alpha_panel, labels)
        
        # Prepare data
        X = alpha_panel.values.astype(np.float32)
        y = labels.values.astype(np.float32).reshape(-1, 1)
        
        # Handle NaN values
        X = np.nan_to_num(X, nan=0.0)
        
        # Split data
        n_val = int(len(X) * validation_split)
        X_train, X_val = X[:-n_val], X[-n_val:]
        y_train, y_val = y[:-n_val], y[-n_val:]
        
        # Convert to tensors
        X_train_tensor = torch.FloatTensor(X_train).to(self.device)
        y_train_tensor = torch.FloatTensor(y_train).to(self.device)
        X_val_tensor = torch.FloatTensor(X_val).to(self.device)
        y_val_tensor = torch.FloatTensor(y_val).to(self.device)
        
        # Create data loader
        train_dataset = TensorDataset(X_train_tensor, y_train_tensor)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        
        # Loss and optimizer
        criterion = nn.BCELoss()
        optimizer = optim.Adam(self.model.parameters(), lr=self.learning_rate)
        
        # Training loop
        train_losses = []
        val_losses = []
        
        for epoch in range(epochs):
            # Train
            self.model.train()
            epoch_loss = 0.0
            
            for batch_X, batch_y in train_loader:
                optimizer.zero_grad()
                outputs = self.model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()
            
            avg_train_loss = epoch_loss / len(train_loader)
            train_losses.append(avg_train_loss)
            
            # Validate
            self.model.eval()
            with torch.no_grad():
                val_outputs = self.model(X_val_tensor)
                val_loss = criterion(val_outputs, y_val_tensor)
                val_losses.append(val_loss.item())
            
            if verbose and (epoch + 1) % 10 == 0:
                logger.info(f"Epoch {epoch + 1}/{epochs}: "
                          f"Train Loss: {avg_train_loss:.4f}, "
                          f"Val Loss: {val_loss.item():.4f}")
        
        # Calculate feature importance
        feature_importance = self._calculate_feature_importance(X_val_tensor)
        
        return {
            "train_loss": train_losses[-1] if train_losses else 0.0,
            "val_loss": val_losses[-1] if val_losses else 0.0,
            "epochs_trained": epochs,
            "feature_importance": feature_importance,
        }
    
    def predict(self, alpha_panel: pd.DataFrame) -> np.ndarray:
        """Predict probability of positive 5-day return.
        
        Args:
            alpha_panel: DataFrame with alpha values
            
        Returns:
            Array of predicted probabilities
        """
        if not HAS_TORCH or self.model is None:
            return self._fallback_predict(alpha_panel)
        
        # Prepare data
        X = alpha_panel.values.astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)
        X_tensor = torch.FloatTensor(X).to(self.device)
        
        # Predict
        self.model.eval()
        with torch.no_grad():
            predictions = self.model(X_tensor).cpu().numpy().flatten()
        
        return predictions
    
    def get_portfolio_weights(self, predictions: np.ndarray,
                             temperature: float = 1.0) -> np.ndarray:
        """Map predictions to long-only portfolio weights.
        
        Args:
            predictions: Predicted probabilities
            temperature: Softmax temperature (higher = more uniform)
            
        Returns:
            Portfolio weights (sum to 1)
        """
        # Apply temperature scaling
        logits = (predictions - 0.5) * temperature
        
        # Softmax for weights
        exp_logits = np.exp(logits - np.max(logits))
        weights = exp_logits / exp_logits.sum()
        
        return weights
    
    def _calculate_feature_importance(self, X_tensor) -> Dict[int, float]:
        """Calculate feature importance using gradient-based method."""
        if not HAS_TORCH or self.model is None:
            return {}
        
        self.model.eval()
        X_tensor.requires_grad = True
        
        # Forward pass
        outputs = self.model(X_tensor)
        
        # Backward pass
        outputs.sum().backward()
        
        # Get gradients
        gradients = X_tensor.grad.abs().mean(dim=0).cpu().numpy()
        
        # Normalize to sum to 1
        if gradients.sum() > 0:
            importance = gradients / gradients.sum()
        else:
            importance = np.ones(len(gradients)) / len(gradients)
        
        return {i: float(v) for i, v in enumerate(importance)}
    
    def _fallback_train(self, alpha_panel: pd.DataFrame,
                       labels: pd.Series) -> Dict[str, float]:
        """Fallback training using simple weighted average."""
        # Simple IC-based weighting
        from scipy import stats as sp_stats
        
        ic_scores = []
        for col in alpha_panel.columns:
            ic, _ = sp_stats.spearmanr(alpha_panel[col].dropna(), 
                                       labels.loc[alpha_panel[col].dropna().index])
            ic_scores.append(ic if np.isfinite(ic) else 0.0)
        
        # Normalize to weights
        ic_array = np.array(ic_scores)
        weights = np.abs(ic_array) / (np.abs(ic_array).sum() + 1e-10)
        
        self._fallback_weights = weights
        
        return {
            "method": "ic_weighted_fallback",
            "mean_abs_ic": float(np.mean(np.abs(ic_scores))),
        }
    
    def _fallback_predict(self, alpha_panel: pd.DataFrame) -> np.ndarray:
        """Fallback prediction using weighted average."""
        if not hasattr(self, '_fallback_weights'):
            self._fallback_weights = np.ones(alpha_panel.shape[1]) / alpha_panel.shape[1]
        
        X = alpha_panel.values.astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)
        
        # Weighted sum
        raw_score = X @ self._fallback_weights
        
        # Sigmoid to map to [0, 1]
        predictions = 1 / (1 + np.exp(-raw_score))
        
        return predictions


# ---------------------------------------------------------------------------
# SHAP Explainer
# ---------------------------------------------------------------------------

class SHAPExplainer:
    """Explain DNN predictions using SHAP values."""
    
    def __init__(self, model: Any, background_data: pd.DataFrame,
                 device: str = "cpu"):
        """Initialize SHAP explainer.
        
        Args:
            model: Trained DNN model
            background_data: Background data for SHAP
            device: Device for computation
        """
        self.model = model
        self.background_data = background_data
        self.device = device
        self.explainer = None
        
        try:
            import shap
            if HAS_TORCH and hasattr(model, 'model'):
                self.explainer = shap.DeepExplainer(
                    model.model, 
                    torch.FloatTensor(background_data.values).to(device)
                )
        except ImportError:
            logger.warning("SHAP not available. Explainer will use fallback.")
        except Exception as e:
            logger.warning(f"SHAP initialization failed: {e}")
    
    def explain(self, alpha_panel: pd.DataFrame) -> Dict[int, float]:
        """Get SHAP values for each alpha factor.
        
        Args:
            alpha_panel: Alpha values to explain
            
        Returns:
            Dictionary mapping alpha index to SHAP value
        """
        if self.explainer is None:
            return self._fallback_explain(alpha_panel)
        
        try:
            X = torch.FloatTensor(alpha_panel.values).to(self.device)
            shap_values = self.explainer.shap_values(X)
            
            # Average across samples
            mean_shap = np.mean(shap_values[0] if isinstance(shap_values, list) 
                               else shap_values, axis=0)
            
            return {i: float(v) for i, v in enumerate(mean_shap)}
        
        except Exception as e:
            logger.warning(f"SHAP explanation failed: {e}")
            return self._fallback_explain(alpha_panel)
    
    def _fallback_explain(self, alpha_panel: pd.DataFrame) -> Dict[int, float]:
        """Fallback explanation using correlation with target."""
        # Simple correlation-based importance
        importance = {}
        for i, col in enumerate(alpha_panel.columns):
            # Use variance as proxy for importance
            importance[i] = float(alpha_panel[col].var())
        
        # Normalize
        total = sum(importance.values())
        if total > 0:
            importance = {k: v / total for k, v in importance.items()}
        
        return importance
    
    def get_dominant_signals(self, shap_values: Dict[int, float],
                            alpha_names: Optional[List[str]] = None,
                            top_k: int = 10) -> List[Tuple[str, float]]:
        """Return top-K alpha factors by absolute SHAP value.
        
        Args:
            shap_values: SHAP values dictionary
            alpha_names: Optional names for alphas
            top_k: Number of top signals to return
            
        Returns:
            List of (name, shap_value) tuples
        """
        # Sort by absolute value
        sorted_items = sorted(shap_values.items(), 
                            key=lambda x: abs(x[1]), 
                            reverse=True)[:top_k]
        
        result = []
        for idx, value in sorted_items:
            name = alpha_names[idx] if alpha_names and idx < len(alpha_names) else f"alpha_{idx}"
            result.append((name, value))
        
        return result


# ---------------------------------------------------------------------------
# MegaAlpha Portfolio
# ---------------------------------------------------------------------------

@dataclass
class MegaAlphaResult:
    """Result of MegaAlpha portfolio construction."""
    composite_signal: pd.Series          # Combined alpha signal
    individual_signals: pd.DataFrame     # Individual alpha signals
    weights: np.ndarray                  # Alpha weights
    predictions: np.ndarray              # DNN predictions
    shap_values: Dict[int, float]        # SHAP values
    ic_score: float                      # IC of composite signal
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "n_alphas": len(self.weights),
            "composite_ic": round(self.ic_score, 6),
            "mean_weight": round(float(np.mean(self.weights)), 4),
            "max_weight": round(float(np.max(self.weights)), 4),
            "weight_std": round(float(np.std(self.weights)), 4),
        }


class MegaAlphaAggregator:
    """Construct MegaAlpha portfolio from mined alphas."""
    
    def __init__(self, n_alphas: int, hidden_dims: List[int] = [128, 64, 32]):
        """Initialize MegaAlpha aggregator.
        
        Args:
            n_alphas: Number of alpha factors
            hidden_dims: DNN hidden dimensions
        """
        self.n_alphas = n_alphas
        self.dnn = DNNAggregator(n_alphas, hidden_dims)
        self.explainer = None
    
    def construct(self, alpha_panel: pd.DataFrame,
                 forward_returns: pd.Series,
                 epochs: int = 100) -> MegaAlphaResult:
        """Construct MegaAlpha portfolio.
        
        Args:
            alpha_panel: Alpha values (n_samples, n_alphas)
            forward_returns: Forward returns for training
            epochs: Training epochs
            
        Returns:
            MegaAlphaResult with composite signal and diagnostics
        """
        # Create binary labels (positive 5-day return)
        labels = (forward_returns > 0).astype(float)
        
        # Align data
        common_idx = alpha_panel.index.intersection(labels.index)
        alpha_aligned = alpha_panel.loc[common_idx]
        labels_aligned = labels.loc[common_idx]
        
        # Train DNN
        train_metrics = self.dnn.train(alpha_aligned, labels_aligned, epochs=epochs)
        
        # Get predictions
        predictions = self.dnn.predict(alpha_aligned)
        
        # Get portfolio weights
        weights = self.dnn.get_portfolio_weights(predictions)
        
        # Compute composite signal
        composite_raw = alpha_aligned.values @ weights
        composite_signal = pd.Series(composite_raw, index=common_idx)
        
        # Calculate IC of composite
        from scipy import stats as sp_stats
        ic, _ = sp_stats.spearmanr(composite_signal.dropna(), 
                                   forward_returns.loc[common_idx].dropna())
        ic_score = ic if np.isfinite(ic) else 0.0
        
        # Get SHAP values
        if self.explainer is None:
            self.explainer = SHAPExplainer(self.dnn, alpha_aligned.iloc[:100])
        
        shap_values = self.explainer.explain(alpha_aligned)
        
        return MegaAlphaResult(
            composite_signal=composite_signal,
            individual_signals=alpha_aligned,
            weights=weights,
            predictions=predictions,
            shap_values=shap_values,
            ic_score=ic_score,
        )
