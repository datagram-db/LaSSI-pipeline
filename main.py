from LaSSI.LaSSI import LaSSI

# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    pipeline = LaSSI("newcastle_orig.yaml", "connection.yaml")
    pipeline.run()
    pipeline.close()
# See PyCharm help at https://www.jetbrains.com/help/pycharm/
