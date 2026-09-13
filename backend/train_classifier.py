"""
Entrena un clasificador de intención (TF-IDF + Regresión Logística) para
el bot. 100% local, sin costo de API — corre en tu propia máquina o en
el servidor donde despliegues el backend.

Uso:
    python train_classifier.py

Genera: model.pkl (el pipeline completo: vectorizador + clasificador)
"""
import unicodedata
import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.metrics import classification_report, confusion_matrix


def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def main():
    df = pd.read_csv("data/training_data.csv")
    df["text_norm"] = df["text"].apply(normalize)

    X_train, X_test, y_train, y_test = train_test_split(
        df["text_norm"], df["label"], test_size=0.2, random_state=42, stratify=df["label"]
    )

    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1)),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
    ])

    pipeline.fit(X_train, y_train)

    print("=== Reporte de evaluación (datos de prueba) ===")
    y_pred = pipeline.predict(X_test)
    print(classification_report(y_test, y_pred, zero_division=0))

    # Con pocos ejemplos, un solo split es ruidoso (solo 29 mensajes en
    # prueba). La validación cruzada promedia sobre varias particiones y da
    # una idea mucho más estable del rendimiento real.
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scores = cross_val_score(pipeline, df["text_norm"], df["label"], cv=cv, scoring="accuracy")
    print(f"\nAccuracy promedio (5-fold CV): {scores.mean():.2f} (+/- {scores.std():.2f})")

    print("\n=== Matriz de confusión (filas = real, columnas = predicho) ===")
    cm = confusion_matrix(y_test, y_pred, labels=pipeline.classes_)
    cm_df = pd.DataFrame(cm, index=pipeline.classes_, columns=pipeline.classes_)
    print(cm_df)

    pipeline.fit(df["text_norm"], df["label"])
    joblib.dump(pipeline, "model.pkl")
    print(f"\nModelo guardado en model.pkl — entrenado con {len(df)} ejemplos.")


if __name__ == "__main__":
    main()