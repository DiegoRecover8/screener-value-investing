# Snapshots de descubrimiento

Este directorio recibirá listas amplias generadas por fecha. Un snapshot de
descubrimiento nunca se convierte automáticamente en universo oficial: antes
se compara, revisa y registra como una nueva versión en `../manifest.json`.

Los archivos `disc_*.csv` contienen los miembros y los `disc_*.json`, su
configuración y controles. Los `checkpoint_*.json` son temporales, reanudables
y quedan fuera de Git; al completar correctamente se eliminan.
