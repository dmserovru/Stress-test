import sys
import psutil
import platform
import time
import threading
import multiprocessing
import random
import os
import subprocess
import ctypes
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, 
                            QWidget, QPushButton, QLabel, QProgressBar, QGroupBox,
                            QComboBox, QSpinBox, QCheckBox, QMessageBox)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt5.QtGui import QFont
import pyqtgraph as pg  # Используем pyqtgraph вместо matplotlib [[8]]
import numpy as np

# Глобальные флаги для определения доступных функций
try:
    import wmi
    has_wmi = True
except ImportError:
    has_wmi = False

# Глобальные переменные для кэширования температуры
_last_temp_check = 0
_last_temp_value = 0

# Функция для проверки прав администратора
def is_admin():
    try:
        if platform.system() == "Windows":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except:
        return False

class MonitoringGraph(pg.PlotWidget):
    """Класс графика на основе pyqtgraph для более высокой производительности [[8]]"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Мониторинг системы")
        self.setLabel('left', 'Загрузка (%)')
        self.setLabel('bottom', 'Время (с)')
        self.setXRange(0, 60)
        self.setYRange(0, 100)
        self.setMouseEnabled(False)
        
        # Линии графиков
        self.cpu_line = self.plot(pen='r', name='CPU')
        self.memory_line = self.plot(pen='b', name='RAM')
        self.disk_line = self.plot(pen='g', name='Disk')
        self.cpu_temp_line = self.plot(pen='y', name='CPU Temp')
        
        # Легенда
        self.addLegend()
        
        # Буферы данных
        self.max_points = 60
        self.data = {
            'cpu': [], 'memory': [], 'disk': [], 'cpu_temp': []
        }

    def update_graph(self, data_history):
        """Обновление графика с использованием pyqtgraph [[8]]"""
        for key in self.data:
            if key in data_history:
                self.data[key] = data_history[key][-self.max_points:]
                
        x = list(range(len(self.data['cpu'])))
        self.cpu_line.setData(x, self.data['cpu'])
        self.memory_line.setData(x, self.data['memory'])
        self.disk_line.setData(x, self.data['disk'])
        
        # Обработка температуры
        if self.data['cpu_temp']:
            temps = [min(t, 100) for t in self.data['cpu_temp']]
            self.cpu_temp_line.setData(x, temps)

class SystemMonitor(QObject):
    update_signal = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.running = False
        self.stress_running = False
        self.data_history = {
            'cpu': [],
            'memory': [],
            'disk': [],
            'cpu_temp': []
        }
        self.max_history = 60  # Хранить 60 точек данных
        self.stress_processes = []
        self.stress_start_time = 0
        self.last_update = {'cpu': 0, 'ram': 0, 'disk': 0}  # Кэш для оптимизации обновления UI
        
    def start_monitoring(self):
        """Запуск мониторинга системы"""
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop)
        self.thread.daemon = True
        self.thread.start()
        
    def stop_monitoring(self):
        """Остановка мониторинга"""
        self.running = False
        self.stop_stress_test()
        
    def start_stress_test(self, cpu=True):
        """Запуск стресс-теста с оптимизацией использования ресурсов"""
        if self.stress_running:
            return
            
        self.stress_running = True
        self.stop_event = threading.Event()
        self.stress_start_time = time.time()
        
        # Создаем и запускаем поток для стресс-теста
        stress_thread = threading.Thread(
            target=self._run_stress_test,
            args=(cpu,)
        )
        stress_thread.daemon = True
        stress_thread.start()
        
    def _run_stress_test(self, cpu):
        """Запуск стресс-теста в отдельном потоке"""
        try:
            # Выбираем метод стресс-теста в зависимости от ОС
            if platform.system() == "Windows":
                cpu_stress(self.stop_event)
            else:
                create_and_run_c_stress(self.stop_event)
        except Exception as e:
            print(f"Ошибка в потоке стресс-теста: {e}")
            
    def stop_stress_test(self):
        """Остановка стресс-теста"""
        if not self.stress_running:
            return
            
        if hasattr(self, 'stop_event'):
            self.stop_event.set()
            
        # Дополнительно убиваем все процессы Python, если они остались
        if platform.system() == "Windows":
            try:
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] == 'python.exe' or proc.info['name'] == 'pythonw.exe':
                            cmdline = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                            if 'heavy_calculation' in cmdline:
                                proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
            except:
                pass
                
        self.stress_processes = []
        self.stress_running = False
        
    def _monitor_loop(self):
        """Основной цикл мониторинга с оптимизированной частотой обновления"""
        last_update = 0
        while self.running:
            try:
                current_time = time.time()
                
                # Сбор данных
                data = self._get_system_data()
                
                # Обновление истории
                for key in self.data_history:
                    if key in data:
                        self.data_history[key].append(data[key])
                        if len(self.data_history[key]) > self.max_history:
                            self.data_history[key].pop(0)
                
                # Редкое обновление UI (не чаще 5 раз в секунду)
                if current_time - last_update >= 0.2:  
                    data['history'] = self.data_history
                    data['stress_running'] = self.stress_running
                    if self.stress_running:
                        data['stress_time'] = time.time() - self.stress_start_time
                    self.update_signal.emit(data)
                    last_update = current_time
                    
                time.sleep(0.05)  # Добавляем небольшую паузу для экономии ресурсов
            except Exception as e:
                print(f"Ошибка в цикле мониторинга: {e}")
                
    def _get_system_data(self):
        """Сбор данных системы с оптимизацией для повышения производительности"""
        data = {}
        
        # CPU usage
        data['cpu'] = psutil.cpu_percent(interval=0.1)  # Уменьшаем интервал для более точных данных
        
        # Memory usage
        memory = psutil.virtual_memory()
        data['memory'] = memory.percent
        data['memory_total'] = self._format_bytes(memory.total)
        data['memory_used'] = self._format_bytes(memory.used)
        
        # Disk usage
        disk = psutil.disk_usage('/')
        data['disk'] = disk.percent
        data['disk_total'] = self._format_bytes(disk.total)
        data['disk_used'] = self._format_bytes(disk.used)
        
        # CPU temperature and frequency
        cpu_info = self._get_cpu_info()
        data['cpu_temp'] = cpu_info.get('temp', 0)
        data['cpu_freq'] = cpu_info.get('freq', 0)
        data['cpu_name'] = cpu_info.get('name', "")
        
        # Проверяем, запущено ли приложение с правами администратора
        data['is_admin'] = is_admin()
        
        return data
        
    def _get_cpu_info(self):
        """Получение информации о CPU с кэшированием температуры для повышения производительности"""
        result = {
            'temp': 0,
            'freq': 0,
            'name': self._get_cpu_name()
        }
        
        try:
            # Быстрый доступ к базовым данным
            result['freq'] = psutil.cpu_freq(percpu=False).current
            
            # Периодический запрос температуры (не чаще раза в 3 секунды)
            global _last_temp_check, _last_temp_value
            current_time = time.time()
            
            if current_time - _last_temp_check >= 3:
                _last_temp_check = current_time
                
                # Платформозависимые методы получения температуры
                if platform.system() == "Windows":
                    if has_wmi:
                        try:
                            w_standard = wmi.WMI(namespace="root\\wmi")
                            temp = w_standard.MSAcpi_ThermalZoneTemperature()[0].CurrentTemperature
                            result['temp'] = float(temp)/10.0 - 273.15
                        except:
                            pass
                elif platform.system() == "Linux":
                    try:
                        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                            result['temp'] = float(f.read())/1000.0
                    except:
                        pass
                        
                _last_temp_value = result['temp']
            else:
                result['temp'] = _last_temp_value
                
        except Exception as e:
            print(f"Ошибка получения информации о CPU: {e}")
            
        return result
        
    def _get_cpu_name(self):
        """Получение имени процессора с оптимизацией"""
        system = platform.system()
        
        # Метод 1: Windows через WMI - улучшенный
        if system == "Windows" and has_wmi:
            try:
                w = wmi.WMI()
                for processor in w.Win32_Processor():
                    return processor.Name.strip()
            except Exception as e:
                print(f"Ошибка при получении имени процессора через WMI: {e}")
                
        # Метод 2: Windows через реестр
        if system == "Windows":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
                processor_name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
                winreg.CloseKey(key)
                if processor_name:
                    return processor_name
            except Exception as e:
                print(f"Ошибка при получении имени процессора через реестр: {e}")
                
        # Метод 3: Linux через /proc/cpuinfo
        elif system == "Linux":
            try:
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if "model name" in line:
                            return line.split(":")[1].strip()
            except Exception as e:
                print(f"Ошибка при получении имени процессора через /proc/cpuinfo: {e}")
                
        # Метод 4: macOS через sysctl
        elif system == "Darwin":
            try:
                sysctl_result = subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], 
                                         stdout=subprocess.PIPE, text=True)
                return sysctl_result.stdout.strip()
            except Exception as e:
                print(f"Ошибка при получении имени процессора через sysctl: {e}")
                
        # Если все методы не сработали
        return platform.processor()
        
    def _format_bytes(self, bytes):
        """Форматирование байтов в человекочитаемый формат"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes < 1024:
                return f"{bytes:.2f} {unit}"
            bytes /= 1024
        return f"{bytes:.2f} PB"

class StressTestWorker(QThread):
    finished = pyqtSignal()
    
    def __init__(self, action, params=None):
        super().__init__()
        self.action = action
        self.params = params or {}
        
    def run(self):
        if self.action == "start":
            self._start_stress_test()
        elif self.action == "stop":
            self._stop_stress_test()
        self.finished.emit()
        
    def _start_stress_test(self):
        monitor = self.params.get("monitor")
        if monitor:
            cpu_enabled = self.params.get("cpu", True)
            monitor.start_stress_test(cpu=cpu_enabled)
            
    def _stop_stress_test(self):
        monitor = self.params.get("monitor")
        if monitor:
            monitor.stop_stress_test()

class SystemMonitorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("System Monitor & Stress Test")
        self.setGeometry(100, 100, 900, 700)
        
        # Инициализация мониторинга
        self.monitor = SystemMonitor()
        self.monitor.update_signal.connect(self.update_ui)
        
        # Инициализация интерфейса
        self.init_ui()
        
        # Проверка прав администратора
        self.check_admin_rights()
        
        # Запуск мониторинга
        self.monitor.start_monitoring()
        
        # Настройка таймера для редкого обновления графика
        self.graph_update_timer = QTimer()
        self.graph_update_timer.timeout.connect(self.update_graph)
        self.graph_update_timer.start(200)  # Обновление графика каждые 200 мс
        
    def check_admin_rights(self):
        """Проверка прав администратора"""
        if platform.system() == "Windows" and not is_admin():
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Предупреждение")
            msg.setText("Приложение запущено без прав администратора")
            msg.setInformativeText("Для отображения температуры CPU нужно запустить от Администратора")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
            
    def init_ui(self):
        # Основной виджет и компоновка
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Кнопки управления
        control_layout = QHBoxLayout()
        self.start_button = QPushButton("Запустить стресс-тест")
        self.start_button.clicked.connect(self.start_stress_test)
        self.start_button.setStyleSheet("font-weight: bold; background-color: #4CAF50; color: white;")
        self.stop_button = QPushButton("Остановить стресс-тест")
        self.stop_button.clicked.connect(self.stop_stress_test)
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet("font-weight: bold; background-color: #f44336; color: white;")
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)
        main_layout.addLayout(control_layout)
        
        # Группа CPU
        cpu_group = QGroupBox("Процессор")
        cpu_layout = QVBoxLayout()
        self.cpu_name_label = QLabel("Процессор: N/A")
        self.cpu_label = QLabel("Загрузка CPU: 0%")
        self.cpu_progress = QProgressBar()
        self.cpu_temp_label = QLabel("Температура CPU: N/A")
        self.cpu_freq_label = QLabel("Частота CPU: N/A")
        cpu_layout.addWidget(self.cpu_name_label)
        cpu_layout.addWidget(self.cpu_label)
        cpu_layout.addWidget(self.cpu_progress)
        cpu_layout.addWidget(self.cpu_temp_label)
        cpu_layout.addWidget(self.cpu_freq_label)
        cpu_group.setLayout(cpu_layout)
        
        # Группа RAM
        ram_group = QGroupBox("Оперативная память")
        ram_layout = QVBoxLayout()
        self.ram_label = QLabel("Загрузка RAM: 0%")
        self.ram_progress = QProgressBar()
        self.ram_details_label = QLabel("Использовано: 0 / 0")
        ram_layout.addWidget(self.ram_label)
        ram_layout.addWidget(self.ram_progress)
        ram_layout.addWidget(self.ram_details_label)
        ram_group.setLayout(ram_layout)
        
        # Группа Disk
        disk_group = QGroupBox("Диск")
        disk_layout = QVBoxLayout()
        self.disk_label = QLabel("Загрузка диска: 0%")
        self.disk_progress = QProgressBar()
        self.disk_details_label = QLabel("Использовано: 0 / 0")
        disk_layout.addWidget(self.disk_label)
        disk_layout.addWidget(self.disk_progress)
        disk_layout.addWidget(self.disk_details_label)
        disk_group.setLayout(disk_layout)
        
        # Компоновка групп
        stats_layout = QHBoxLayout()
        stats_layout.addWidget(cpu_group)
        stats_layout.addWidget(ram_group)
        stats_layout.addWidget(disk_group)
        main_layout.addLayout(stats_layout)
        
        # Статус стресс-теста и таймер
        status_layout = QHBoxLayout()
        self.stress_status_label = QLabel("Статус: Стресс-тест не запущен")
        self.stress_status_label.setAlignment(Qt.AlignCenter)
        self.stress_status_label.setStyleSheet("font-weight: bold;")
        self.stress_timer_label = QLabel("Время: 00:00:00")
        self.stress_timer_label.setAlignment(Qt.AlignCenter)
        self.stress_timer_label.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self.stress_status_label)
        status_layout.addWidget(self.stress_timer_label)
        main_layout.addLayout(status_layout)
        
        # График
        self.graph = MonitoringGraph(self)
        main_layout.addWidget(self.graph)
        
        # Информация о системе
        system_info = f"Система: {platform.system()} {platform.version()}\n"
        system_info += f"Процессор: {platform.processor()}\n"
        system_info += f"Python: {platform.python_version()}"
        system_info_label = QLabel(system_info)
        system_info_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(system_info_label)
        
    def update_graph(self):
        """Обновление графика с оптимизированной частотой"""
        if hasattr(self.monitor, 'data_history'):
            self.graph.update_graph(self.monitor.data_history)
            
    def start_stress_test(self):
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stress_status_label.setText("Статус: Стресс-тест запущен")
        self.stress_status_label.setStyleSheet("font-weight: bold; color: red;")
        self.start_button.setStyleSheet("font-weight: bold; background-color: #cccccc; color: #666666;")
        self.stop_button.setStyleSheet("font-weight: bold; background-color: #f44336; color: white;")
        
        # Запуск стресс-теста
        self.monitor.start_stress_test(cpu=True)
        
    def stop_stress_test(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.stress_status_label.setText("Статус: Стресс-тест остановлен")
        self.stress_status_label.setStyleSheet("font-weight: bold; color: green;")
        self.start_button.setStyleSheet("font-weight: bold; background-color: #4CAF50; color: white;")
        self.stop_button.setStyleSheet("font-weight: bold; background-color: #cccccc; color: #666666;")
        
        # Остановка стресс-теста
        self.monitor.stop_stress_test()
        
    def format_time(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
    def update_ui(self, data):
        try:
            # Обновление CPU
            if abs(data['cpu'] - self.monitor.last_update.get('cpu', 0)) > 0.5:
                self.cpu_label.setText(f"Загрузка CPU: {data['cpu']:.1f}%")
                self.cpu_progress.setValue(int(data['cpu']))
                self.monitor.last_update['cpu'] = data['cpu']
                
            if 'cpu_name' in data and data['cpu_name']:
                self.cpu_name_label.setText(f"Процессор: {data['cpu_name']}")
                
            if 'cpu_temp' in data and data['cpu_temp'] > 0:
                self.cpu_temp_label.setText(f"Температура CPU: {data['cpu_temp']:.1f}°C")
            else:
                if platform.system() == "Windows" and not data.get('is_admin', False):
                    self.cpu_temp_label.setText("Температура CPU: Для отображения температуры CPU нужно запустить от Администратора")
                else:
                    self.cpu_temp_label.setText("Температура CPU: N/A")
                    
            if 'cpu_freq' in data and data['cpu_freq'] > 0:
                self.cpu_freq_label.setText(f"Частота CPU: {data['cpu_freq']:.2f} МГц")
            else:
                self.cpu_freq_label.setText("Частота CPU: N/A")
                
            # Обновление RAM
            if abs(data['memory'] - self.monitor.last_update.get('memory', 0)) > 0.5:
                self.ram_label.setText(f"Загрузка RAM: {data['memory']:.1f}%")
                self.ram_progress.setValue(int(data['memory']))
                self.ram_details_label.setText(f"Использовано: {data['memory_used']} / {data['memory_total']}")
                self.monitor.last_update['memory'] = data['memory']
                
            # Обновление Disk
            if abs(data['disk'] - self.monitor.last_update.get('disk', 0)) > 0.5:
                self.disk_label.setText(f"Загрузка диска: {data['disk']:.1f}%")
                self.disk_progress.setValue(int(data['disk']))
                self.disk_details_label.setText(f"Использовано: {data['disk_used']} / {data['disk_total']}")
                self.monitor.last_update['disk'] = data['disk']
                
            # Обновление таймера стресс-теста
            if 'stress_time' in data:
                formatted_time = self.format_time(data['stress_time'])
                self.stress_timer_label.setText(f"Время: {formatted_time}")
            else:
                self.stress_timer_label.setText("Время: 00:00:00")
                
        except Exception as e:
            print(f"Ошибка при обновлении UI: {e}")
            
    def closeEvent(self, event):
        try:
            self.monitor.stop_monitoring()
        except Exception as e:
            print(f"Ошибка при закрытии приложения: {e}")
        event.accept()

# Функции для стресс-теста
def cpu_stress(stop_event):
    """Функция для интенсивной нагрузки CPU с оптимизацией"""
    processes = []
    try:
        cpu_count = multiprocessing.cpu_count()
        
        # Создаем startupinfo для скрытого запуска
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            
        # Запускаем процессы для каждого ядра
        for _ in range(cpu_count):
            process = subprocess.Popen(
                [sys.executable, '-c', stress_code],
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                shell=False,  
                startupinfo=startupinfo
            )
            processes.append(process)
            
        # Ждем сигнала остановки
        while not stop_event.is_set():
            time.sleep(0.5)
            
    except Exception as e:
        print(f"Ошибка при запуске стресс-теста CPU: {e}")
    finally:
        # Завершаем все процессы
        for process in processes:
            try:
                process.terminate()
            except:
                pass

# Код для стресс-теста CPU
stress_code = """
import time
import math
import random

def heavy_calculation():
    x = 0
    while True:
        x = math.sin(random.random() * 10) * math.cos(random.random() * 10)
        x = math.sqrt(abs(x)) + math.pow(abs(x), 3)
        if random.random() > 0.99:
            lst = [random.random() for _ in range(10000)]
            lst.sort()

start_time = time.time()
while True:
    heavy_calculation()
    if time.time() - start_time > 3600:
        break
"""

def create_and_run_c_stress(stop_event):
    """Создание и запуск C-программы для максимальной нагрузки CPU"""
    # C-код для интенсивной нагрузки
    c_code = """
    #include <stdio.h>
    #include <stdlib.h>
    #include <math.h>
    #include <time.h>
    
    int main() {
        int i, j;
        double result = 0.0;
        time_t start_time = time(NULL);
        
        while (1) {
            for (i = 0; i < 10000000; i++) {
                result = sin(i) * cos(i);
                result = sqrt(fabs(result)) + pow(fabs(result), 3);
                if (i % 1000000 == 0) {
                    double* array = (double*)malloc(100000 * sizeof(double));
                    for (j = 0; j < 100000; j++) {
                        array[j] = sin((double)j);
                    }
                    free(array);
                }
            }
            if (difftime(time(NULL), start_time) > 3600) {
                break;
            }
        }
        return 0;
    }
    """
    
    try:
        import tempfile
        
        # Создаем временные файлы с безопасными именами
        with tempfile.NamedTemporaryFile(suffix='.c', delete=False) as c_file_obj:
            c_file = c_file_obj.name
            c_file_obj.write(c_code.encode('utf-8'))
            
        if platform.system() == "Windows":
            exe_file = c_file.replace('.c', '.exe')
        else:
            exe_file = c_file.replace('.c', '')
            
        # Создаем startupinfo для скрытого запуска
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            
        # Компилируем C-код
        if platform.system() == "Windows":
            compile_cmd = ['gcc', c_file, '-o', exe_file, '-lm']
        else:
            compile_cmd = ['gcc', c_file, '-o', exe_file, '-lm', '-O3']
            
        subprocess.run(compile_cmd, check=True, shell=False, 
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      startupinfo=startupinfo)
                      
        # Запускаем скомпилированную программу
        processes = []
        cpu_count = multiprocessing.cpu_count()
        for _ in range(cpu_count):
            process = subprocess.Popen([exe_file], 
                                      stdout=subprocess.PIPE, 
                                      stderr=subprocess.PIPE,
                                      shell=False,
                                      startupinfo=startupinfo)
            processes.append(process)
            
        # Ждем сигнала остановки
        while not stop_event.is_set():
            time.sleep(0.5)
            
        # Завершаем процессы
        for process in processes:
            try:
                process.terminate()
            except:
                pass
                
        # Удаляем временные файлы
        try:
            os.remove(c_file)
            if os.path.exists(exe_file):
                os.remove(exe_file)
        except:
            pass
            
    except Exception as e:
        print(f"Ошибка при запуске C-стресс-теста: {e}")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    window = SystemMonitorApp()
    window.show()
    sys.exit(app.exec_())
