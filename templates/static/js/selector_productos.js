// Buscador de productos para recetas
document.addEventListener('DOMContentLoaded', function() {
    const searchInput = document.getElementById('buscar-producto');
    const productoSelect = document.getElementById('producto_id');
    const resultadosDiv = document.getElementById('resultados-busqueda');
    const nuevoProductoBtn = document.getElementById('btn-nuevo-producto');
    const nuevoProductoForm = document.getElementById('nuevo-producto-form');
    
    if (!searchInput) return;
    
    // Obtener todos los productos del select original
    const productos = [];
    const opciones = productoSelect.options;
    for (let i = 0; i < opciones.length; i++) {
        if (opciones[i].value) {
            productos.push({
                id: opciones[i].value,
                nombre: opciones[i].text,
                unidad: opciones[i].getAttribute('data-unidad') || ''
            });
        }
    }
    
    // Función de búsqueda
    function buscarProductos(termino) {
        if (termino.length < 2) {
            resultadosDiv.innerHTML = '<div class="sin-resultados">Escribe al menos 2 caracteres</div>';
            return;
        }
        
        const terminoLower = termino.toLowerCase();
        const resultados = productos.filter(p => 
            p.nombre.toLowerCase().includes(terminoLower)
        );
        
        if (resultados.length === 0) {
            resultadosDiv.innerHTML = `
                <div class="sin-resultados">
                    No hay resultados. 
                    <a href="#" id="crear-desde-busqueda">Crear "${termino}"</a>
                </div>
            `;
            
            document.getElementById('crear-desde-busqueda').addEventListener('click', function(e) {
                e.preventDefault();
                document.getElementById('nuevo-producto-nombre').value = termino;
                nuevoProductoForm.style.display = 'block';
                resultadosDiv.innerHTML = '';
                searchInput.value = termino;
            });
            return;
        }
        
        let html = '<div class="resultados-lista">';
        resultados.slice(0, 5).forEach(p => {
            html += `
                <div class="resultado-item" data-id="${p.id}" data-nombre="${p.nombre}">
                    ${p.nombre} <small>(${p.unidad})</small>
                </div>
            `;
        });
        html += '</div>';
        resultadosDiv.innerHTML = html;
        
        // Añadir evento click a cada resultado
        document.querySelectorAll('.resultado-item').forEach(item => {
            item.addEventListener('click', function() {
                const id = this.dataset.id;
                const nombre = this.dataset.nombre;
                productoSelect.value = id;
                searchInput.value = nombre;
                resultadosDiv.innerHTML = '';
            });
        });
    }
    
    // Evento de búsqueda
    searchInput.addEventListener('input', function() {
        buscarProductos(this.value);
    });
    
    // Mostrar/ocultar formulario nuevo producto
    nuevoProductoBtn.addEventListener('click', function() {
        nuevoProductoForm.style.display = 'block';
    });
    
    // Cancelar nuevo producto
    document.getElementById('cancelar-nuevo-producto').addEventListener('click', function() {
        nuevoProductoForm.style.display = 'none';
        document.getElementById('nuevo-producto-nombre').value = '';
    });
});