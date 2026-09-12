import pygame
import pygame_gui
import sys

# Inicializar Pygame
pygame.init()

# Configurações da tela
SCREEN_WIDTH = 1000
SCREEN_HEIGHT = 600
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("Selecionar e Arrastar Objetos")

# Áreas da tela
MAIN_AREA = pygame.Rect(250, 50, 700, 500)
SIDEBAR = pygame.Rect(20, 50, 200, 500)

# Cores
WHITE = (255, 255, 255)
LIGHT_GRAY = (200, 200, 200)
GRAY = (150, 150, 150)
BLUE = (0, 120, 215)

# Gerenciador de UI
manager = pygame_gui.UIManager((SCREEN_WIDTH, SCREEN_HEIGHT))

# "Base de dados" de objetos
database = {
    "Formas": [
        {"nome": "Círculo", "cor": (255, 0, 0), "tipo": "circle"},
        {"nome": "Retângulo", "cor": (0, 255, 0), "tipo": "rect"},
        {"nome": "Triângulo", "cor": (0, 0, 255), "tipo": "triangle"}
    ],
    "Ícones": [
        {"nome": "Casa", "cor": (200, 100, 50), "tipo": "house"},
        {"nome": "Árvore", "cor": (34, 139, 34), "tipo": "tree"},
        {"nome": "Carro", "cor": (70, 70, 220), "tipo": "car"}
    ]
}

# Criar dropdown para categorias
categories = list(database.keys())
dropdown = pygame_gui.elements.UIDropDownMenu(
    options_list=categories,
    starting_option=categories[0],
    relative_rect=pygame.Rect(20, 10, 200, 30),
    manager=manager
)

# Lista de objetos na sidebar (será atualizada conforme a categoria selecionada)
object_buttons = []

# Objetos colocados na área principal
placed_objects = []
selected_object = None
dragging = False
offset_x, offset_y = 0, 0


# Função para atualizar a sidebar com os objetos da categoria selecionada
def update_sidebar(category):
    global object_buttons

    # Limpar botões existentes
    for button in object_buttons:
        button.kill()
    object_buttons = []

    # Adicionar novos botões para cada objeto na categoria
    y_pos = 60
    for obj in database[category]:
        button = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect(20, y_pos, 200, 40),
            text=obj["nome"],
            manager=manager
        )
        button.object_data = obj  # Armazenar os dados do objeto no botão
        object_buttons.append(button)
        y_pos += 50


# Atualizar sidebar inicialmente
update_sidebar(categories[0])


# Função para desenhar objetos na área principal
def draw_objects():
    for obj in placed_objects:
        if obj["tipo"] == "circle":
            pygame.draw.circle(screen, obj["cor"], (obj["x"], obj["y"]), 30)
        elif obj["tipo"] == "rect":
            pygame.draw.rect(screen, obj["cor"], (obj["x"] - 30, obj["y"] - 30, 60, 60))
        elif obj["tipo"] == "triangle":
            points = [(obj["x"], obj["y"] - 30),
                      (obj["x"] - 30, obj["y"] + 30),
                      (obj["x"] + 30, obj["y"] + 30)]
            pygame.draw.polygon(screen, obj["cor"], points)
        elif obj["tipo"] == "house":
            # Desenho simplificado de uma casa
            pygame.draw.rect(screen, obj["cor"], (obj["x"] - 25, obj["y"] - 15, 50, 30))
            pygame.draw.polygon(screen, obj["cor"],
                                [(obj["x"] - 30, obj["y"] - 15),
                                 (obj["x"], obj["y"] - 35),
                                 (obj["x"] + 30, obj["y"] - 15)])
        # Adicione outros tipos conforme necessário


# Loop principal
clock = pygame.time.Clock()
running = True

while running:
    time_delta = clock.tick(60) / 1000.0

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        # Eventos do pygame_gui
        manager.process_events(event)

        # Eventos do mouse
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1:  # Botão esquerdo
                # Verificar se clicou em um objeto na área principal
                for obj in reversed(placed_objects):  # Verificar do último ao primeiro (ordem z)
                    if obj["tipo"] == "circle":
                        if ((event.pos[0] - obj["x"]) ** 2 + (event.pos[1] - obj["y"]) ** 2 <= 30 ** 2):
                            selected_object = obj
                            offset_x = obj["x"] - event.pos[0]
                            offset_y = obj["y"] - event.pos[1]
                            dragging = True
                            break
                    elif obj["tipo"] in ["rect", "house"]:
                        if (obj["x"] - 30 <= event.pos[0] <= obj["x"] + 30 and
                                obj["y"] - 30 <= event.pos[1] <= obj["y"] + 30):
                            selected_object = obj
                            offset_x = obj["x"] - event.pos[0]
                            offset_y = obj["y"] - event.pos[1]
                            dragging = True
                            break
                    elif obj["tipo"] == "triangle":
                        # Verificação simplificada para triângulo
                        if (obj["y"] - 30 <= event.pos[1] <= obj["y"] + 30 and
                                abs(event.pos[0] - obj["x"]) <= 30 - (event.pos[1] - obj["y"]) / 3):
                            selected_object = obj
                            offset_x = obj["x"] - event.pos[0]
                            offset_y = obj["y"] - event.pos[1]
                            dragging = True
                            break

        elif event.type == pygame.MOUSEMOTION:
            if dragging and selected_object:
                # Atualizar posição do objeto sendo arrastado
                selected_object["x"] = event.pos[0] + offset_x
                selected_object["y"] = event.pos[1] + offset_y

        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                dragging = False
                selected_object = None

        # Eventos do UI
        if event.type == pygame_gui.UI_DROP_DOWN_MENU_CHANGED:
            if event.ui_element == dropdown:
                update_sidebar(event.text)

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element in object_buttons:
                # Criar uma cópia do objeto selecionado para colocar na área principal
                new_obj = event.ui_element.object_data.copy()
                new_obj["x"] = MAIN_AREA.centerx
                new_obj["y"] = MAIN_AREA.centery
                placed_objects.append(new_obj)

    # Atualizar UI
    manager.update(time_delta)

    # Desenhar
    screen.fill(WHITE)

    # Desenhar áreas
    pygame.draw.rect(screen, LIGHT_GRAY, MAIN_AREA, 2)
    pygame.draw.rect(screen, LIGHT_GRAY, SIDEBAR, 2)

    # Desenhar objetos
    draw_objects()

    # Desenhar UI
    manager.draw_ui(screen)

    pygame.display.flip()

pygame.quit()
sys.exit()
